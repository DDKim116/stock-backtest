"""원본 일별 파일들을 합쳐 분석용 prices.parquet 를 만든다.

수정주가 계산:
  KRX 의 '전일 대비'는 액면분할·병합·유무상증자 등을 반영한 기준가 대비 값이다.
  따라서 기준가 = 종가 - 전일대비, 조정계수 = 기준가 / 전일 실제 종가.
  계수가 1 이 아니면 그날 권리 변동이 있었던 것이고, 그 이전 가격 전부에 계수를 곱한다.
  거래량은 반대로 나눈다.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ADJ_TOL = 0.002  # 반올림 오차 허용 범위


def adjust(df: pd.DataFrame) -> pd.DataFrame:
    """df: code, date 로 정렬, 거래정지일 제거 완료. diff 컬럼이 있으면 수정주가를 계산."""
    df = df.sort_values(["code", "date"], kind="stable").reset_index(drop=True)
    same = df["code"].to_numpy()[1:] == df["code"].to_numpy()[:-1]
    prev_close = np.empty(len(df))
    prev_close[0] = np.nan
    prev_close[1:] = np.where(same, df["close"].to_numpy()[:-1], np.nan)
    factor = np.ones(len(df))
    if "diff" in df:
        base = df["close"].to_numpy() - df["diff"].to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            f = base / prev_close
        ok = np.isfinite(f) & (f > 0) & (np.abs(f - 1) > ADJ_TOL)
        factor[ok] = f[ok]
    # 각 행의 누적 계수 = 이후(자기 자신 제외) 모든 계수의 곱
    g = df["code"].to_numpy()
    s = pd.Series(np.log(factor))
    # 그룹별 역방향 누적합 - 자기 자신
    rev_cum = s[::-1].groupby(g[::-1]).cumsum()[::-1].to_numpy()
    cum = np.exp(rev_cum - np.log(factor))
    df["adj"] = cum
    df["close_raw"] = df["close"]
    for c in ("open", "high", "low", "close"):
        df[c] = df[c] * cum
    df["volume"] = df["volume"] / cum
    df["is_adj_event"] = factor != 1.0
    return df


def build(root: Path, log=print) -> Path:
    raw = root / "raw"
    files = sorted(raw.glob("*/*.parquet")) + sorted(raw.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"{raw} 에 원본 파일이 없습니다. 먼저 수집하세요")
    con = duckdb.connect()
    df = con.execute(
        "SELECT * FROM read_parquet(?, union_by_name=true)", [[str(f) for f in files]]
    ).df()
    con.close()
    n0 = len(df)
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    # 거래정지일(시가·고가·저가 0 또는 거래량 0) 제거 → '전일' 은 직전 거래일을 뜻한다
    halted = (df["open"].fillna(0) <= 0) | (df["high"].fillna(0) <= 0) | (df["low"].fillna(0) <= 0) \
        | (df["volume"].fillna(0) <= 0)
    df = df[~halted].drop_duplicates(["code", "date"], keep="last")
    log(f"원본 {n0:,}행 → 거래정지 등 제외 후 {len(df):,}행")
    df = adjust(df)
    n_events = int(df["is_adj_event"].sum())
    log(f"수정주가 이벤트 {n_events:,}건 반영")
    cols = ["date", "code", "name", "market", "open", "high", "low", "close", "volume", "value", "mktcap",
            "close_raw", "adj", "is_adj_event"]
    df = df[[c for c in cols if c in df]]
    out = root / "prices.parquet"
    tmp = out.with_suffix(".tmp.parquet")
    df.to_parquet(tmp, index=False)
    tmp.replace(out)
    meta = {
        "rows": int(len(df)),
        "codes": int(df["code"].nunique()),
        "start": str(df["date"].min().date()),
        "end": str(df["date"].max().date()),
        "built_at": dt.datetime.now().isoformat(timespec="seconds"),
        "adj_events": n_events,
    }
    (root / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    log(f"저장: {out} ({meta['codes']:,}종목, {meta['start']} ~ {meta['end']})")
    return out
