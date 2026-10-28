"""시장 지수 (코스피·코스닥·S&P 500·나스닥) 종가 수집과 연도별·월별 등락 분석.

- 검증용 종목 데이터와는 따로 data/index/<KEY>.csv (date, close) 로 저장한다.
- 한국: KRX OpenAPI 지수 서비스 ('KOSPI 시리즈 일별시세정보', 'KOSDAQ 시리즈 일별시세정보' 이용 신청 필요)
- 미국: 야후 파이낸스 (^GSPC, ^IXIC)
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import pandas as pd
import requests

from .. import config, krx_status
from . import krx

INDICES: dict[str, dict] = {
    "KOSPI": {"name": "코스피", "group": "kr", "endpoint": "kospi_dd_trd", "idx_nm": "코스피"},
    "KOSDAQ": {"name": "코스닥", "group": "kr", "endpoint": "kosdaq_dd_trd", "idx_nm": "코스닥"},
    "SP500": {"name": "S&P 500", "group": "us", "ticker": "^GSPC"},
    "NASDAQ": {"name": "나스닥", "group": "us", "ticker": "^IXIC"},
}
GROUPS = [{"key": "kr", "name": "한국"}, {"key": "us", "name": "미국"}]
KR_START = dt.date(2009, 12, 1)  # 2010년 등락률의 기준(2009년 마지막 종가)까지
US_START = "1985-01-01"
IDX_BASE = "https://data-dbg.krx.co.kr/svc/apis/idx"


def index_dir() -> Path:
    return config.DATA_DIR / "index"


def path(key: str) -> Path:
    return index_dir() / f"{key}.csv"


def load(key: str) -> pd.Series | None:
    p = path(key)
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["date"])
    s = df.dropna().drop_duplicates("date", keep="last").set_index("date")["close"].sort_index()
    return s[s > 0] if len(s) else None


def _save(key: str, s: pd.Series) -> None:
    index_dir().mkdir(parents=True, exist_ok=True)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    pd.DataFrame({"date": s.index.strftime("%Y-%m-%d"), "close": s.values.round(4)}).to_csv(path(key), index=False)


# ---------------------------------------------------------------- 미국 (야후)
def update_us(log=print) -> None:
    import yfinance as yf

    for key, info in INDICES.items():
        if info["group"] != "us":
            continue
        df = yf.download(info["ticker"], start=US_START, auto_adjust=False, progress=False, threads=False)
        if df is None or df.empty:
            log(f"{info['name']}: 받지 못함")
            continue
        close = df["Close"]
        if isinstance(close, pd.DataFrame):  # 열이 (Close, 티커) 로 오는 경우
            close = close.iloc[:, 0]
        close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
        _save(key, close.dropna())
        log(f"{info['name']}: {close.index[0].date()} ~ {close.index[-1].date()} ({len(close):,}일)")


# ---------------------------------------------------------------- 한국 (KRX)
def fetch_kr_day(day: dt.date, key: str, info: dict, sess: requests.Session) -> float | None:
    r = sess.get(f"{IDX_BASE}/{info['endpoint']}", params={"basDd": day.strftime("%Y%m%d")},
                 headers={"AUTH_KEY": key}, timeout=30)
    if r.status_code in (401, 403):
        raise krx.KrxError(f"'{info['name']}' 지수 서비스 이용 신청이 아직 승인되지 않았습니다 (KRX {r.status_code})")
    if r.status_code != 200:
        raise krx.KrxError(f"KRX 응답 오류 {r.status_code}: {r.text[:200]}")
    rows = r.json().get("OutBlock_1") or []
    if not rows:
        return None  # 휴장일
    names = [str(x.get("IDX_NM", "")).strip() for x in rows]
    want = info["idx_nm"]
    row = rows[names.index(want)] if want in names else None
    if row is None:  # 이름이 조금 다르게 올 때: '코스피' 로 시작하는 가장 짧은 이름 (예: 코스피 200 보다 코스피)
        cands = sorted((n for n in names if n.startswith(want)), key=len)
        if not cands:
            raise krx.KrxError(f"KRX 응답에서 '{want}' 지수를 찾지 못했습니다: {names[:10]}")
        row = rows[names.index(cands[0])]
    v = krx._to_num(pd.Series([row.get("CLSPRC_IDX")])).iloc[0]
    return None if pd.isna(v) else float(v)


def _kr_trading_days(start: dt.date, end: dt.date) -> list[dt.date]:
    """종목 데이터 수집 기록에서 거래일만 (없으면 평일 전부)."""
    m = krx.load_manifest(config.dataset_dir("kr"))
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            n = m.get(d.isoformat())
            if n is None or n > 0:
                days.append(d)
        d += dt.timedelta(days=1)
    return days


def update_kr(api_key: str, refetch_days: int = 7, max_calls: int = 9000, log=print) -> None:
    try:
        _update_kr(api_key, refetch_days, max_calls, log)
    except krx.KrxError as e:
        krx_status.record_error("index", str(e), krx.is_auth_error(e))
        raise
    krx_status.record_ok("index")


def _update_kr(api_key: str, refetch_days: int, max_calls: int, log) -> None:
    if not api_key:
        raise krx.KrxError("KRX_API_KEY 가 비어 있습니다")
    sess = requests.Session()
    today = dt.date.today()
    calls = 0
    for key, info in INDICES.items():
        if info["group"] != "kr":
            continue
        s = load(key)
        start = KR_START if s is None else max(KR_START, (s.index[-1] - pd.Timedelta(days=refetch_days)).date())
        got: dict[pd.Timestamp, float] = {}
        days = _kr_trading_days(start, today)
        for i, d in enumerate(days):
            if calls >= max_calls:
                log(f"호출 한도({max_calls}) 도달. 내일 이어서 받습니다.")
                break
            for attempt in range(4):
                try:
                    v = fetch_kr_day(d, api_key, info, sess)
                    break
                except (requests.RequestException, ValueError) as e:
                    if attempt == 3:
                        raise
                    log(f"재시도 {d} {key}: {e}")
                    time.sleep(2 ** (attempt + 1))
            calls += 1
            if v is not None:
                got[pd.Timestamp(d)] = v
            if (i + 1) % 250 == 0:
                log(f"{info['name']}: {d} 까지 ({i + 1}/{len(days)}일)")
                _save(key, pd.concat([s, pd.Series(got)]) if s is not None else pd.Series(got))
            time.sleep(0.05)
        if got:
            new = pd.Series(got)
            s = pd.concat([s, new]) if s is not None else new
            _save(key, s)
        log(f"{info['name']}: {len(got)}일 저장")


def update(log=print) -> None:
    """미국은 매번 전체를 다시 받고(가볍다), 한국은 이어받는다. 한쪽이 실패해도 다른 쪽은 진행."""
    try:
        update_us(log)
    except Exception as e:  # noqa: BLE001
        log(f"미국 지수 갱신 실패: {e}")
    try:
        update_kr(config.KRX_API_KEY, log=log)
    except krx.KrxError as e:
        log(f"한국 지수: {e}")
    except Exception as e:  # noqa: BLE001
        log(f"한국 지수 갱신 실패: {e}")
    try:
        update_ohlc(log)
    except Exception as e:  # noqa: BLE001
        log(f"지수 일봉 갱신 실패: {e}")


# ---------------------------------------------------------------- 종목 데이터에 붙이기 (조건식의 '지수')
MARKET_INDEX = {
    "kr": {"KOSPI": "KOSPI", "KOSDAQ": "KOSDAQ", "KONEX": "KOSDAQ"},
    "demo": {"KOSPI": "KOSPI", "KOSDAQ": "KOSDAQ"},
    "us": {"NASDAQ": "NASDAQ", "NYSE": "SP500", "AMEX": "SP500"},
}


def mtime(dataset: str) -> float:
    keys = set(MARKET_INDEX.get(dataset, {}).values())
    return max((path(k).stat().st_mtime for k in keys if path(k).exists()), default=0.0)


def attach(panel, dataset: str) -> bool:
    """panel.cols['index'] = 그 종목이 속한 시장의 지수 종가 (그날 값, 휴장 등으로 없으면 최근 7일 안의 값)."""
    import numpy as np

    mapping = MARKET_INDEX.get(dataset, {})
    n = len(panel.dates)
    vals = np.full(n, np.nan)
    found = False
    for market, key in mapping.items():
        s = load(key)
        if s is None:
            continue
        m = panel.markets == market
        if not m.any():
            continue
        sd = s.index.to_numpy(dtype="datetime64[D]")
        sv = s.to_numpy(dtype=float)
        rd = panel.dates[m]
        k = np.searchsorted(sd, rd, side="right") - 1
        ok = k >= 0
        kk = np.where(ok, k, 0)
        ok &= (rd - sd[kk]).astype(int) <= 7
        v = np.where(ok, sv[kk], np.nan)
        vals[m] = v
        found = True
    if found:
        panel.cols["index"] = vals
    return found


def update_kr_evening(day: dt.date | None = None, log=print) -> None:
    """국내 지수 오늘 값을 야후(^KS11, ^KQ11)로 임시로 채운다. 다음 날 아침 KRX 갱신이 덮어쓴다."""
    import yfinance as yf

    for key, ticker in (("KOSPI", "^KS11"), ("KOSDAQ", "^KQ11")):
        s = load(key)
        if s is None:
            continue
        df = yf.download(ticker, start=(s.index[-1] - pd.Timedelta(days=3)).date().isoformat(),
                         auto_adjust=False, progress=False, threads=False)
        if df is None or df.empty:
            log(f"{key} 지수: 야후에서 받지 못함")
            continue
        close = df["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
        new = close[close.index > s.index[-1]].dropna()
        if day:
            new = new[new.index <= pd.Timestamp(day)]
        if len(new):
            _save(key, pd.concat([s, new]))
            log(f"{key} 지수: {', '.join(d.strftime('%m-%d') for d in new.index)} 임시값 추가")


# ---------------------------------------------------------------- 일봉 (차트용 시가·고가·저가)
YAHOO = {"KOSPI": "^KS11", "KOSDAQ": "^KQ11", "SP500": "^GSPC", "NASDAQ": "^IXIC"}


def ohlc_path(key: str) -> Path:
    return index_dir() / f"{key}_ohlc.csv"


def update_ohlc(log=print, keys=None) -> None:
    """차트용 일봉을 야후에서 받는다 (한 번에 전체, 가볍다). 종가 분석은 계속 KRX 종가 파일을 쓴다."""
    import yfinance as yf

    for key in keys or YAHOO:
        start = KR_START.isoformat() if INDICES[key]["group"] == "kr" else US_START
        df = yf.download(YAHOO[key], start=start, auto_adjust=False, progress=False, threads=False)
        if df is None or df.empty:
            log(f"{INDICES[key]['name']} 일봉: 받지 못함")
            continue
        if isinstance(df.columns, pd.MultiIndex):  # 열이 (Open, 티커) 로 오는 경우
            df.columns = df.columns.get_level_values(0)
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        out = df[["Open", "High", "Low", "Close"]].dropna()
        out = out[(out > 0).all(axis=1)]
        index_dir().mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"date": out.index.strftime("%Y-%m-%d"), "open": out["Open"].round(2).values,
                      "high": out["High"].round(2).values, "low": out["Low"].round(2).values,
                      "close": out["Close"].round(2).values}).to_csv(ohlc_path(key), index=False)
        log(f"{INDICES[key]['name']} 일봉: {len(out):,}일")


def bars(key: str) -> list[dict]:
    """차트용 일봉. 종가는 분석과 같은 파일(KRX 등) 값을 쓰고, 시가·고가·저가는 야후 일봉에서.
    야후에 없는 날은 종가만으로 (시가=고가=저가=종가)."""
    close = load(key)
    if close is None:
        return []
    p = ohlc_path(key)
    o = pd.read_csv(p, parse_dates=["date"]).drop_duplicates("date", keep="last").set_index("date") if p.exists() else None
    df = pd.DataFrame({"c": close})
    if o is not None:
        df = df.join(o[["open", "high", "low"]], how="left")
        # 야후 값과 종가가 크게 다르면(다른 날 값 등) 쓰지 않는다
        bad = (df["open"] / df["c"] - 1).abs() > 0.15
        df.loc[bad, ["open", "high", "low"]] = None
    else:
        df["open"] = df["high"] = df["low"] = None
    df["open"] = df["open"].fillna(df["c"])
    df["high"] = df[["high", "open", "c"]].max(axis=1)
    df["low"] = df[["low", "open", "c"]].min(axis=1)
    return [{"t": d.strftime("%Y-%m-%d"), "o": round(float(r.open), 2), "h": round(float(r.high), 2),
             "l": round(float(r.low), 2), "c": round(float(r.c), 2)} for d, r in df.iterrows()]


# ---------------------------------------------------------------- 분석
def _r(x, nd=2):
    return None if x is None or pd.isna(x) else round(float(x), nd)


def analyze(s: pd.Series, today: dt.date | None = None) -> dict:
    """종가 시리즈 → 연도별(월별 포함) 등락률.

    등락률은 앞 기간 마지막 거래일 종가 대비. 데이터 첫 해는 기준으로만 쓰고 결과에서 뺀다.
    """
    today = today or dt.date.today()
    last = s.index[-1]
    years = []
    by_year = s.groupby(s.index.year)
    prev_close = None
    for y, ys in by_year:
        if prev_close is None:  # 첫 해는 기준(전년 마지막 종가)이 없어 다음 해의 기준으로만 쓴다
            prev_close = ys.iloc[-1]
            continue
        base = prev_close
        months = []
        mprev = base
        for mth in range(1, 13):
            ms = ys[ys.index.month == mth]
            if ms.empty:
                months.append(None)
                continue
            mbase = mprev
            months.append({
                "m": mth,
                "ret": _r((ms.iloc[-1] / mbase - 1) * 100),
                "close": _r(ms.iloc[-1]),
                "partial": bool(y == last.year and mth == last.month and (y, mth) == (today.year, today.month)),
            })
            mprev = ms.iloc[-1]
        path_ = pd.concat([pd.Series([base]), ys.reset_index(drop=True)])
        dd = (path_ / path_.cummax() - 1).min() * 100
        years.append({
            "year": int(y),
            "ret": _r((ys.iloc[-1] / base - 1) * 100),
            "base": _r(base),
            "close": _r(ys.iloc[-1]),
            "high": _r(ys.max()),
            "low": _r(ys.min()),
            "mdd": _r(dd),
            "days": int(len(ys)),
            "partial": bool(y == today.year),
            "months": months,
        })
        prev_close = ys.iloc[-1]
    full = [y for y in years if not y["partial"]]
    rets = [y["ret"] for y in full]
    stats = None
    if full:
        start_v = full[0]["base"]
        end_v = full[-1]["close"]
        n = len(full)
        stats = {
            "n": n,
            "from": full[0]["year"],
            "to": full[-1]["year"],
            "avg": _r(sum(rets) / n),
            "cagr": _r(((end_v / start_v) ** (1 / n) - 1) * 100),
            "up": sum(1 for r in rets if r > 0),
            "best": max(full, key=lambda y: y["ret"])["year"],
            "worst": min(full, key=lambda y: y["ret"])["year"],
        }
    return {"first": str(years[0]["year"]) if years else None, "last": last.date().isoformat(),
            "last_close": _r(s.iloc[-1]), "years": years, "stats": stats}


def summary(group: str) -> dict:
    out = []
    for key, info in INDICES.items():
        if info["group"] != group:
            continue
        s = load(key)
        item = {"key": key, "name": info["name"], "available": s is not None and len(s) > 1}
        if item["available"]:
            item.update(analyze(s))
        out.append(item)
    return {"group": group, "groups": GROUPS, "indices": out}
