"""홈의 공포·탐욕 지수, 시총 지도(트리맵), 시장 탭의 계기판.

- 미장: VIX(야후 ^VIX, 'S&P 500 옵션으로 본 앞으로 30일 변동성 예상'). 20 넘으면 불안, 30 넘으면 공포.
- 국장: VKOSPI(코스피200 변동성지수, KRX '파생상품지수 시세정보' 이용 신청 필요)가 있으면 같이 보여 주고,
  공포·탐욕 점수(0~100)는 우리 데이터로 만든다 (CNN Fear & Greed 와 같은 생각):
  모멘텀(코스피 / 125일선) · 시장 폭(상승비율 20일 평균) · 신고가-신저가 · 변동성(낮을수록 탐욕) · 고점 대비 낙폭.
  항목마다 지난 2년(500거래일) 안에서 오늘 값이 몇 % 위치인지(0~100)를 내고 평균한다.
- 계기판: 박스 정도 · 200일선 · 대형주 쏠림 · 시장 폭 · v2b 최근 신호. 규칙을 바꾸는 신호가 아니라 '지금 모습' 참고용.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from . import config
from .data import indices

VKOSPI = {"name": "코스피200 변동성지수", "endpoint": "drvprod_dd_trd", "idx_nm": "코스피 200 변동성지수"}
LOOK = 500


# ---------------------------------------------------------------- 받기 (지수 갱신 때 같이)
def _err_path():
    return indices.index_dir() / "VKOSPI.err"


def update_fear(log=print) -> None:
    """VIX 전체, VKOSPI 최근 이어받기. 실패해도 다른 갱신은 막지 않는다."""
    try:
        vix = indices.yahoo_close("^VIX", "1990-01-01")
        if len(vix):
            indices._save("VIX", vix)
            log(f"VIX: ~{vix.index[-1].date()}")
    except Exception as e:  # noqa: BLE001
        log(f"VIX 갱신 실패: {e}")
    if not config.KRX_API_KEY:
        return
    import requests

    from .data import krx

    s = indices.load("VKOSPI")
    today = dt.date.today()
    start = (s.index[-1] - pd.Timedelta(days=7)).date() if s is not None else today - dt.timedelta(days=550)
    got = {} if s is None else dict(s.items())
    sess = requests.Session()
    try:
        for d in indices._kr_trading_days(start, today)[:600]:
            v = indices.fetch_kr_day(d, config.KRX_API_KEY, VKOSPI, sess)
            if v is not None:
                got[pd.Timestamp(d)] = v
        if got:
            indices._save("VKOSPI", pd.Series(got).sort_index())
            log(f"VKOSPI: ~{max(got).date()}")
        _err_path().unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "승인" in msg or "401" in msg or "403" in msg:
            msg = "KRX openapi 에서 '파생상품지수 시세정보' 이용 신청이 필요합니다"
        _err_path().parent.mkdir(parents=True, exist_ok=True)
        _err_path().write_text(msg[:300])
        log(f"VKOSPI: {msg}")


# ---------------------------------------------------------------- 계산 도우미
def _pct_rank(s: pd.Series, look: int = LOOK) -> pd.Series:
    """그날 값이 지난 look 일 안에서 몇 % 위치인지 (0~100)."""
    return s.rolling(look, min_periods=120).apply(lambda a: (a[:-1] < a[-1]).mean() * 100 if len(a) > 1 else np.nan,
                                                   raw=True)


def _label(score: float) -> str:
    if score < 25:
        return "극단적 공포"
    if score < 45:
        return "공포"
    if score <= 55:
        return "중립"
    if score <= 75:
        return "탐욕"
    return "극단적 탐욕"


def _stock_frame(panel, market: str) -> pd.DataFrame:
    m = (panel.markets == market) & ~panel.is_preferred & ~panel.is_spac
    idx = np.flatnonzero(m)
    return pd.DataFrame({"row": idx, "date": panel.dates[idx], "close": panel.cols["close"][idx],
                         "code": panel.codes[idx].astype(str)})


def highs_lows(panel, market: str = "KOSPI") -> pd.Series:
    """날마다 (52주 신고가 종목 수 - 신저가 종목 수) / 종목 수 × 100."""
    df = _stock_frame(panel, market)
    g = df.groupby("code")["close"]
    hi = g.transform(lambda s: s.rolling(250, min_periods=200).max())
    lo = g.transform(lambda s: s.rolling(250, min_periods=200).min())
    ok = hi.notna()
    df = df[ok]
    net = ((df["close"] >= hi[ok]).astype(int) - (df["close"] <= lo[ok]).astype(int)).groupby(df["date"])
    return (net.sum() / net.count() * 100).rename(index=lambda d: pd.Timestamp(d))


def kr_fear(panel) -> dict:
    k = indices.load("KOSPI")
    b = getattr(panel, "breadth", None) or {}
    if k is None or "KOSPI" not in b:
        return {"available": False}
    x = b["KOSPI"]
    adv = pd.Series(x["adv"], index=pd.DatetimeIndex(x["dates"])).rolling(20, min_periods=10).mean()
    vk = indices.load("VKOSPI")
    vol = vk if vk is not None and len(vk) > 200 else k.pct_change().rolling(20).std() * np.sqrt(252) * 100
    comps = {
        "모멘텀": (k / k.rolling(125).mean() - 1) * 100,
        "시장 폭": adv,
        "신고가-신저가": highs_lows(panel),
        "변동성": vol,
        "고점 대비": (k / k.rolling(250, min_periods=120).max() - 1) * 100,
    }
    idx = k.index
    scores = {}
    for name, s in comps.items():
        s = s.reindex(idx.union(s.index)).ffill().reindex(idx)
        r = _pct_rank(s)
        scores[name] = 100 - r if name == "변동성" else r
    sc = pd.DataFrame(scores)
    total = sc.mean(axis=1, skipna=True).dropna()
    if total.empty:
        return {"available": False}
    last = total.index[-1]
    unit = {"모멘텀": "% (125일선 대비)", "시장 폭": "% (20일 평균 오른 종목)", "신고가-신저가": "% (종목 수 대비)",
            "변동성": " (VKOSPI)" if vk is not None and len(vk) > 200 else "% (20일 실제 변동성, 연)", "고점 대비": "%"}
    hist = total.iloc[-250:]
    return {
        "available": True, "as_of": str(last.date()), "score": round(float(total.iloc[-1]), 1),
        "label": _label(float(total.iloc[-1])),
        "week_ago": round(float(total.iloc[-6]), 1) if len(total) > 6 else None,
        "month_ago": round(float(total.iloc[-22]), 1) if len(total) > 22 else None,
        "components": [{"name": n, "value": None if pd.isna(comps[n].reindex([last], method="ffill").iloc[0]) else
                        round(float(comps[n].reindex([last], method="ffill").iloc[0]), 1),
                        "unit": unit[n], "score": None if pd.isna(sc[n].iloc[-1]) else round(float(sc[n].iloc[-1]))}
                       for n in comps],
        "history": {"dates": [str(d.date()) for d in hist.index], "score": [round(float(v), 1) for v in hist]},
        "vkospi": None if vk is None else {"value": round(float(vk.iloc[-1]), 2), "date": str(vk.index[-1].date()),
                                          "prev": round(float(vk.iloc[-2]), 2) if len(vk) > 1 else None},
        "vkospi_note": _err_path().read_text() if _err_path().exists() else None,
    }


def us_fear() -> dict:
    v = indices.load("VIX")
    if v is None or len(v) < 2:
        return {"available": False}
    val = float(v.iloc[-1])
    y = v.iloc[-250:]
    label = "안정" if val < 15 else "보통" if val < 20 else "불안" if val < 30 else "공포"
    sp = indices.load("SP500")
    trend = None
    if sp is not None and len(sp) > 200:
        trend = round(float(sp.iloc[-1] / sp.rolling(200).mean().iloc[-1] - 1) * 100, 1)
    return {"available": True, "as_of": str(v.index[-1].date()), "value": round(val, 2),
            "prev": round(float(v.iloc[-2]), 2), "label": label,
            "year_low": round(float(y.min()), 2), "year_high": round(float(y.max()), 2),
            "pct_rank": round(float((v.iloc[-500:] < val).mean() * 100)), "sp500_vs_200": trend,
            "history": {"dates": [str(d.date()) for d in y.index], "value": [round(float(x), 2) for x in y]}}


def treemap(panel, market: str, top: int) -> dict:
    """마지막 날 시총 상위 top 종목: 시총·1일/1주/1달 등락."""
    df = _stock_frame(panel, market)
    if df.empty or "mktcap" not in panel.cols:
        return {"market": market, "items": []}
    last = df["date"].max()
    now = df[df["date"] == last].copy()
    now["cap"] = panel.cols["mktcap"][now["row"].to_numpy()]
    now = now[np.isfinite(now["cap"]) & (now["cap"] > 0)].nlargest(top, "cap")
    close, pos = panel.cols["close"], panel.pos
    items = []
    for r in now.itertuples():
        ch = {}
        for key, n in (("d1", 1), ("w1", 5), ("m1", 20)):
            ch[key] = round(float(close[r.row] / close[r.row - n] - 1) * 100, 2) if pos[r.row] >= n else None
        items.append({"code": r.code, "name": str(panel.names[r.row]), "cap": float(r.cap), **ch})
    return {"market": market, "as_of": str(pd.Timestamp(last).date()), "items": items}


def gauge(panel, runner) -> dict:
    k = indices.load("KOSPI")
    if k is None or len(k) < 250:
        return {"available": False}
    hi, lo = float(k.iloc[-250:].max()), float(k.iloc[-250:].min())
    ma200 = float(k.rolling(200).mean().iloc[-1])
    # 대형주 쏠림: 최근 60거래일, 지금 시총 상위 10(시총 가중) vs 101위 아래(동일 가중)
    df = _stock_frame(panel, "KOSPI")
    last = df["date"].max()
    now = df[df["date"] == last].copy()
    now["cap"] = panel.cols["mktcap"][now["row"].to_numpy()] if "mktcap" in panel.cols else np.nan
    rows = now["row"].to_numpy()
    ok = panel.pos[rows] >= 60
    now = now[ok]
    now["r60"] = panel.cols["close"][now["row"].to_numpy()] / panel.cols["close"][now["row"].to_numpy() - 60] - 1
    now = now.sort_values("cap", ascending=False).reset_index(drop=True)
    t10 = now.iloc[:10]
    top10 = float((t10["r60"] * t10["cap"]).sum() / t10["cap"].sum()) * 100 if len(t10) else None
    rest = float(now.iloc[100:]["r60"].clip(-0.9, 3).mean()) * 100 if len(now) > 100 else None
    b = (getattr(panel, "breadth", None) or {}).get("KOSPI")
    adv20 = float(np.nanmean(b["adv"][-20:])) if b is not None else None
    # v2b 최근 20거래일 신호
    n_sig, sig_days = None, None
    try:
        from .research import _family_spec
        from .research2 import _signals
        from .research7 import V2B

        sig = _signals(runner, _family_spec(V2B, ["코스피", "코스닥"]))
        days = np.unique(panel.dates)[-20:]
        d = panel.dates[sig]
        recent = d[d >= days[0]]
        n_sig, sig_days = int(len(recent)), int(len(np.unique(recent)))
    except Exception:  # noqa: BLE001
        pass
    box = hi / lo
    trend = (float(k.iloc[-1]) / ma200 - 1) * 100
    gap = None if top10 is None or rest is None else top10 - rest
    hints = []
    if n_sig is not None:
        hints.append(("v2b", f"최근 20거래일 신호 {n_sig}건({sig_days}일) — 흔들리는 장, v2b 가 일하는 때" if n_sig
                      else "최근 20거래일 신호 없음 — 조용한 장, v2b 는 쉬는 중"))
    if gap is not None:
        if gap >= 10:
            hints.append(("실적 가속 중형주", f"대형주 쏠림 강함 (상위 10이 보통 종목보다 {gap:+.0f}%p) — 이 전략이 지수보다 약했던 환경 (2025~ 같은 장)"))
        elif box <= 1.25:
            hints.append(("실적 가속 중형주", "박스장 + 쏠림 약함 — 이 전략이 지수보다 강했던 환경 (2021.7~2024 같은 장)"))
        else:
            hints.append(("실적 가속 중형주", f"쏠림 보통 ({gap:+.0f}%p) — 특별히 유리하거나 불리하지 않음"))
    hints.append(("주도주·지수", "코스피 200일선 위 — 주도주 계좌라면 보유 구간" if trend > 0
                  else f"코스피 200일선 아래({trend:+.1f}%) — 주도주 계좌라면 현금 구간"))
    return {
        "available": True, "as_of": str(k.index[-1].date()), "kospi": round(float(k.iloc[-1]), 2),
        "box": {"ratio": round(box, 2), "is_box": box <= 1.25, "high": round(hi, 2), "low": round(lo, 2),
                "pos": round((float(k.iloc[-1]) - lo) / (hi - lo) * 100) if hi > lo else None},
        "trend": {"ma200": round(ma200, 2), "pct": round(trend, 1)},
        "skew": {"top10": None if top10 is None else round(top10, 1), "rest": None if rest is None else round(rest, 1),
                 "gap": None if gap is None else round(gap, 1)},
        "breadth": None if adv20 is None else round(adv20, 1),
        "v2b": {"signals": n_sig, "days": sig_days},
        "hints": [{"who": w, "text": t} for w, t in hints],
    }


# ---------------------------------------------------------------- 미국 시총 지도 (미국 갱신 때 만들어 둔다)
ETF_WORDS = ("ETF", "ETN", "Fund", "iShares", "SPDR", "ProShares", "Direxion", "Vanguard", "Invesco", "Index",
             "Portfolio", "Notes", "Warrant", "Preferred", "Bitcoin Trust", "Ether")


def _us_map_path():
    return config.DATA_DIR / "us_map.json"


def build_us_map(root, top: int = 100, log=print) -> None:
    """미국 종목 중 최근 거래대금이 큰 250개의 주식 수(야후, 1주일에 한 번)로 시가총액을 내고 상위 top 개 지도를 저장.
    과거 시가총액은 없어도 지도에는 지금 값만 있으면 된다."""
    import json

    p = root / "prices.parquet"
    if not p.exists():
        return
    df = pd.read_parquet(p, columns=["date", "code", "name", "close", "value"])
    last = df["date"].max()
    df = df[df["date"] >= last - pd.Timedelta(days=45)]
    names = df.drop_duplicates("code", keep="last").set_index("code")["name"].astype(str)
    stock = ~names.str.contains("|".join(ETF_WORDS), case=False, regex=True)
    vol = df[df["date"] >= last - pd.Timedelta(days=30)].groupby("code")["value"].mean()
    cand = vol[vol.index.isin(names[stock].index)].nlargest(250).index.tolist()
    sp = config.DATA_DIR / "us_shares.json"
    cache = json.loads(sp.read_text()) if sp.exists() else {}
    fresh = cache.get("_updated") and (dt.date.today() - dt.date.fromisoformat(cache["_updated"])).days < 7
    if not fresh or any(c not in cache for c in cand[:150]):
        import yfinance as yf

        for c in cand:
            if fresh and c in cache:
                continue
            try:
                sh = yf.Ticker(c).fast_info["shares"]
                if sh:
                    cache[c] = float(sh)
            except Exception:  # noqa: BLE001
                continue
        cache["_updated"] = dt.date.today().isoformat()
        sp.write_text(json.dumps(cache))
    piv = df[df["code"].isin(cand)].pivot_table(index="date", columns="code", values="close").sort_index()
    items = []
    for c in cand:
        if c not in cache or c not in piv or not np.isfinite(piv[c].iloc[-1]):
            continue
        s = piv[c].dropna()
        ch = {k: (round(float(s.iloc[-1] / s.iloc[-1 - n] - 1) * 100, 2) if len(s) > n else None)
              for k, n in (("d1", 1), ("w1", 5), ("m1", 20))}
        items.append({"code": c, "name": names.get(c, c), "cap": float(cache[c] * s.iloc[-1]), **ch})
    items.sort(key=lambda x: -x["cap"])
    out = {"market": "US", "as_of": str(pd.Timestamp(last).date()), "items": items[:top], "currency": "USD"}
    _us_map_path().write_text(json.dumps(out, ensure_ascii=False))
    log(f"미국 시총 지도: {len(out['items'])}종목 · {out['as_of']}")


def us_map() -> dict:
    import json

    p = _us_map_path()
    return json.loads(p.read_text()) if p.exists() else {"market": "US", "items": []}
