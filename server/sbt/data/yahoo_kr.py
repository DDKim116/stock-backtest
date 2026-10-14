"""야후 파이낸스의 국내 시세 — 당일 저녁에 임시로 채우기 위한 보조 경로 (D안).

공식 KRX OpenAPI 는 전날 자료를 다음 날 아침에 주므로, 저녁에는 야후 값으로 먼저 채우고
다음 날 아침 OpenAPI 갱신(최근 며칠 다시 받기)으로 덮어쓰는 용도.
티커: 코스피 = 005930.KS, 코스닥 = 247540.KQ
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config
from . import us

SUFFIX = {"KOSPI": ".KS", "KOSDAQ": ".KQ"}
BIG = ["005930", "000660", "373220", "207940", "005380", "035420", "035720", "247540", "086520", "196170"]


def _raw_file(day: dt.date | None) -> Path | None:
    raw = config.dataset_dir("kr") / "raw"
    if day:
        p = raw / str(day.year) / f"{day.strftime('%Y%m%d')}.parquet"
        return p if p.exists() else None
    files = sorted(raw.glob("*/*.parquet"))
    return files[-1] if files else None


def test(day: dt.date, n: int = 300) -> None:
    """야후 값이 KRX 공식 값과 얼마나 맞는지 확인 (저장하지 않음)."""
    official = _raw_file(day)
    src = official or _raw_file(None)
    if src is None:
        print("국내 원본 데이터가 없습니다")
        return
    base = pd.read_parquet(src)
    base = base[base["market"].isin(SUFFIX) & (base["volume"].fillna(0) > 0)]
    print(f"종목 목록: {src.name} 기준 {len(base):,}종목" + ("" if official else f" (공식 {day} 자료는 아직 없음 → 비교 없이 값만 표시)"))
    rng = np.random.default_rng(0)
    pick = list(dict.fromkeys(BIG + list(rng.choice(base["code"].to_numpy(), size=min(n, len(base)), replace=False))))
    base = base.set_index("code")
    pick = [c for c in pick if c in base.index]
    tickers = [c + SUFFIX[base.at[c, "market"]] for c in pick]

    t0 = time.time()
    got: dict[str, pd.DataFrame] = {}
    for i in range(0, len(tickers), us.BATCH):
        got.update(us._download(tickers[i:i + us.BATCH], start=(day - dt.timedelta(days=7)).isoformat()))
    took = time.time() - t0
    rows = []
    for c, t in zip(pick, tickers):
        df = got.get(t)
        if df is None:
            continue
        r = df[df["date"] == pd.Timestamp(day)]
        if len(r):
            rows.append({"code": c, **{k: float(r.iloc[-1][k]) for k in ["open", "high", "low", "close", "volume"]}})
    y = pd.DataFrame(rows).set_index("code") if rows else pd.DataFrame()
    print(f"야후 요청 {len(tickers):,}종목 → 받은 종목 {len(got):,}, {day} 값이 있는 종목 {len(y):,} ({took:.0f}초)")
    print(f"전 종목(약 {len(base):,}개)을 받으면 약 {took / max(1, len(tickers)) * len(base) / 60:.1f}분 예상")
    if y.empty:
        print(f"{day} 야후 값이 아직 없습니다 (장 마감 후 시간이 더 지나야 할 수 있음)")
        return
    if not official:
        show = y.head(10).join(base[["name"]])
        print(show.to_string())
        return
    o = base.loc[y.index]
    def pct_ok(col, tol):
        d = (y[col] / o[col] - 1).abs()
        return (d <= tol).mean() * 100
    print("KRX 공식 값과 비교 (같은 종목·같은 날):")
    for col in ["open", "high", "low", "close"]:
        print(f"  {col:6s} 정확히 일치 {pct_ok(col, 1e-9):5.1f}% · 0.5% 이내 {pct_ok(col, 0.005):5.1f}%")
    print(f"  volume 5% 이내 {pct_ok('volume', 0.05):5.1f}% · 20% 이내 {pct_ok('volume', 0.2):5.1f}%")
    bad = (y["close"] / o["close"] - 1).abs().sort_values(ascending=False)
    bad = bad[bad > 0.005].head(8)
    if len(bad):
        print("종가가 0.5% 넘게 다른 종목:")
        for c in bad.index:
            print(f"  {c} {o.at[c, 'name']}: 공식 {o.at[c, 'close']:,.0f} / 야후 {y.at[c, 'close']:,.0f}")
    show = y.loc[[c for c in BIG if c in y.index]].join(o[["name", "close", "volume"]], rsuffix="_krx")
    print(show[["name", "close", "close_krx", "volume", "volume_krx"]].to_string())
