"""미국 주식 수집기 (Yahoo Finance, yfinance).

- 종목 목록: 나스닥 트레이더의 상장 종목 파일 (NASDAQ, NYSE, NYSE American). ETF·테스트 종목·워런트·유닛·권리·우선주 제외
- 가격: 액면분할만 반영된 일봉 (배당 미반영, 국내 데이터와 같은 기준)
- 원본은 data/us/raw/{티커}.parquet 로 종목마다 한 파일
- 무료 데이터라 이미 상장폐지된 종목은 받을 수 없다. 다만 수집을 시작한 뒤 상장폐지된 종목은
  기존 파일이 남아 있으므로 그 이후로는 데이터에 계속 포함된다.
"""
from __future__ import annotations

import datetime as dt
import io
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
OTHER_EXCHANGE = {"N": "NYSE", "A": "AMEX"}  # P(Arca)·Z(BATS)·V(IEX) 는 대부분 ETF 라 제외
EXCLUDE_NAME = r"\b(?:Warrants?|Units?|Rights?|Preferred|Notes? due|Debentures?|Depositary Shares? representing)\b"
FX_TICKER = "KRW=X"
BATCH = 80


class UsError(RuntimeError):
    pass


def _read_symdir(url: str) -> pd.DataFrame:
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    lines = [ln for ln in r.text.splitlines() if not ln.startswith("File Creation Time")]
    return pd.read_csv(io.StringIO("\n".join(lines)), sep="|", dtype=str).fillna("")


def parse_symbols(nasdaq: pd.DataFrame, other: pd.DataFrame) -> pd.DataFrame:
    a = nasdaq[(nasdaq["Test Issue"] == "N") & (nasdaq["ETF"] == "N")]
    a = pd.DataFrame({"symbol": a["Symbol"], "name": a["Security Name"], "market": "NASDAQ"})
    b = other[(other["Test Issue"] == "N") & (other["ETF"] == "N") & other["Exchange"].isin(OTHER_EXCHANGE)]
    b = pd.DataFrame({"symbol": b["ACT Symbol"], "name": b["Security Name"], "market": b["Exchange"].map(OTHER_EXCHANGE)})
    df = pd.concat([a, b], ignore_index=True)
    df = df[~df["symbol"].str.contains(r"[$^]", regex=True)]
    df = df[~df["name"].str.contains(EXCLUDE_NAME, case=False, regex=True)]
    # 클래스 주식 표기: BRK.B → 야후 BRK-B
    df["symbol"] = df["symbol"].str.replace(".", "-", regex=False).str.strip()
    df["name"] = df["name"].str.replace(r"\s*-\s*(Class [A-Z] )?Common Stock.*$", "", regex=True).str.strip()
    return df.drop_duplicates("symbol").reset_index(drop=True)


def fetch_symbols() -> pd.DataFrame:
    return parse_symbols(_read_symdir(NASDAQ_LISTED), _read_symdir(OTHER_LISTED))


def update_symbols(root: Path, log=print) -> pd.DataFrame:
    """현재 목록을 받아 지금까지 본 모든 종목 목록(symbols.parquet)에 합친다."""
    cur = fetch_symbols()
    cur["listed"] = True
    p = root / "symbols.parquet"
    if p.exists():
        old = pd.read_parquet(p)
        old = old[~old["symbol"].isin(cur["symbol"])].assign(listed=False)
        allsym = pd.concat([cur, old], ignore_index=True)
    else:
        allsym = cur
    root.mkdir(parents=True, exist_ok=True)
    allsym.to_parquet(p, index=False)
    log(f"종목 목록: 현재 상장 {len(cur):,} / 누적 {len(allsym):,}")
    return allsym


# ---------------------------------------------------------------- 가격

def _download(tickers: list[str], start: str | None = None, period: str | None = None) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    kw = dict(group_by="ticker", auto_adjust=False, actions=False, threads=True, progress=False, timeout=30)
    for attempt in range(4):
        try:
            data = yf.download(tickers, start=start, period=period, **kw) if start else \
                yf.download(tickers, period=period or "1mo", **kw)
            break
        except Exception as e:  # 야후 일시 차단 등
            if attempt == 3:
                raise UsError(f"야후 다운로드 실패: {e}")
            time.sleep(10 * (attempt + 1))
    out: dict[str, pd.DataFrame] = {}
    if data is None or data.empty:
        return out
    for t in tickers:
        try:
            sub = data[t] if isinstance(data.columns, pd.MultiIndex) else data
        except KeyError:
            continue
        sub = sub.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])
        if sub.empty:
            continue
        sub = sub.reset_index().rename(columns={"Date": "date", "index": "date"})
        sub["date"] = pd.to_datetime(sub["date"]).dt.tz_localize(None).dt.normalize()
        out[t] = sub[["date", "open", "high", "low", "close", "volume"]]
    return out


def _raw(root: Path, t: str) -> Path:
    return root / "raw" / f"{t}.parquet"


def collect(root: Path, start: str = "2010-01-01", refetch: bool = False, log=print) -> int:
    """아직 받지 않은 종목의 전체 기간을 받는다. 반환: 저장한 종목 수."""
    syms = update_symbols(root, log)
    todo = [t for t in syms.loc[syms["listed"], "symbol"] if refetch or not _raw(root, t).exists()]
    log(f"받을 종목 {len(todo):,}개")
    (root / "raw").mkdir(parents=True, exist_ok=True)
    saved = 0
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        got = _download(batch, start=start)
        for t, df in got.items():
            df.to_parquet(_raw(root, t), index=False)
            saved += 1
        log(f"{min(i + BATCH, len(todo)):,}/{len(todo):,} (저장 {saved:,})")
        time.sleep(2)
    collect_fx(root, start)
    return saved


def collect_fx(root: Path, start: str = "2010-01-01") -> None:
    got = _download([FX_TICKER, "SPY"], start=start)
    if FX_TICKER in got:
        got[FX_TICKER][["date", "close"]].rename(columns={"close": "fx"}).to_parquet(root / "fx.parquet", index=False)


def update(root: Path, period: str = "1mo", log=print) -> int:
    """최근 한 달을 받아 이어 붙인다. 겹치는 기간 가격이 달라졌으면(분할 등) 그 종목은 전체를 다시 받는다."""
    syms = update_symbols(root, log)
    listed = list(syms.loc[syms["listed"], "symbol"])
    refetch, appended = [], 0
    for i in range(0, len(listed), BATCH):
        batch = listed[i:i + BATCH]
        got = _download(batch, period=period)
        for t, new in got.items():
            p = _raw(root, t)
            if not p.exists():
                refetch.append(t)
                continue
            old = pd.read_parquet(p)
            m = old.merge(new, on="date", suffixes=("_o", "_n"))
            if len(m):
                ratio = (m["close_n"] / m["close_o"]).to_numpy()
                if np.nanmax(np.abs(ratio - 1)) > 0.005:
                    refetch.append(t)
                    continue
            merged = pd.concat([old[~old["date"].isin(new["date"])], new]).sort_values("date")
            merged.to_parquet(p, index=False)
            appended += 1
        time.sleep(1)
    if refetch:
        log(f"전체 다시 받기 {len(refetch)}종목 (신규 상장·분할 등)")
        for i in range(0, len(refetch), BATCH):
            for t, df in _download(refetch[i:i + BATCH], start="2010-01-01").items():
                df.to_parquet(_raw(root, t), index=False)
    collect_fx(root)
    log(f"갱신 {appended:,}종목, 다시 받기 {len(refetch):,}종목")
    return appended


def build(root: Path, log=print) -> Path:
    files = sorted((root / "raw").glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"{root / 'raw'} 에 원본이 없습니다. 먼저 수집하세요")
    syms = pd.read_parquet(root / "symbols.parquet").set_index("symbol")
    frames = []
    for f in files:
        t = f.stem
        if t not in syms.index:
            continue
        df = pd.read_parquet(f)
        df["code"] = t
        df["name"] = syms.at[t, "name"]
        df["market"] = syms.at[t, "market"]
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    n0 = len(df)
    bad = (df[["open", "high", "low", "close"]].le(0).any(axis=1)) | df["volume"].fillna(0).le(0) \
        | df[["open", "high", "low", "close"]].isna().any(axis=1)
    df = df[~bad].drop_duplicates(["code", "date"], keep="last")
    df["high"] = df[["open", "high", "low", "close"]].max(axis=1)
    df["low"] = df[["open", "high", "low", "close"]].min(axis=1)
    df["value"] = df["close"] * df["volume"]
    df["close_raw"] = df["close"]
    df["adj"] = 1.0
    df["is_adj_event"] = False
    fxp = root / "fx.parquet"
    if fxp.exists():
        fx = pd.read_parquet(fxp).sort_values("date")
        df = df.sort_values("date")
        df = pd.merge_asof(df, fx, on="date", direction="backward")
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    log(f"원본 {n0:,}행 → 정리 후 {len(df):,}행, {df['code'].nunique():,}종목")
    out = root / "prices.parquet"
    tmp = out.with_suffix(".tmp.parquet")
    df.to_parquet(tmp, index=False)
    tmp.replace(out)
    import json

    meta = {
        "rows": int(len(df)), "codes": int(df["code"].nunique()),
        "start": str(df["date"].min().date()), "end": str(df["date"].max().date()),
        "built_at": dt.datetime.now().isoformat(timespec="seconds"), "currency": "USD",
        "has_fx": "fx" in df,
    }
    (root / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return out
