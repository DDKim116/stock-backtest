"""경제 지표로 '위험한 때' 피하기: 금리차 역전 · 신용 스프레드 · 실업률 상승 · 금리 인상기.

  python -m sbt.data.cli macro-test     (먼저 fred-update)

FRED 지표는 '발표되어 알 수 있었던 날' 기준 (매달 지표는 한 달쯤 뒤). 오늘 종가로 판단해 다음 날부터 반영.
신호가 켜져 있는 동안은 현금(그때 미국 기준금리만큼 이자), 꺼지면 다시 산다. 사고팔 때 0.1%.
자산: S&P500(VOO) · 나스닥100(QQQ) · QLD(2배) · 비트코인(2015~). 배당 포함(ETF 수정주가, 그 전은 지수).
1부. 규칙별: 그냥 보유 대비 배수 · 연 수익 · 최대 낙폭 · 들고 있던 비율 · 사고판 횟수.
2부. 신호가 새로 켜진 뒤 S&P500 6개월 · 1년 수익 (그냥 아무 날 평균과 비교).
3부. 지금 각 지표.
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import config
from .data import fred
from .research12 import load as load_us
from .research20 import load as load_coins
from .research20 import weekdays

COST = 0.001


def signals(idx: pd.DatetimeIndex) -> dict[str, pd.Series]:
    """날마다 '위험' 여부 (그날 알 수 있던 값). idx 에 맞춰 앞 값으로 채운다."""
    def daily(sid: str) -> pd.Series | None:
        s = fred.load(sid, known=True)
        if s is None or s.empty:
            return None
        return s.reindex(s.index.union(idx)).ffill().reindex(idx)

    out: dict[str, pd.Series] = {}
    t2 = daily("T10Y2Y")
    if t2 is not None:
        out["금리차 역전 (10년-2년 < 0)"] = t2 < 0
    t3 = daily("T10Y3M")
    if t3 is not None:
        out["금리차 역전 (10년-3개월 < 0)"] = t3 < 0
    baa = fred.load("BAA10Y", known=True)
    if baa is not None:
        z = (baa - baa.rolling(252, min_periods=120).mean()) / baa.rolling(252, min_periods=120).std()
        out["신용 스프레드 긴장 (1년 평균 + 1표준편차 위)"] = (z > 1).reindex(z.index.union(idx)).ffill().reindex(idx).fillna(False)
    un = fred.load("UNRATE", known=True)
    if un is not None:
        a3 = un.rolling(3).mean()
        sahm = a3 - a3.shift(1).rolling(12).min()
        out["실업률 상승 (3개월 평균이 1년 저점 +0.5%p)"] = (sahm >= 0.5).reindex(sahm.index.union(idx)).ffill().reindex(idx).fillna(False)
    dff = daily("DFF")
    if dff is not None:
        out["금리 인상기 (기준금리 6개월 새 +0.5%p 이상)"] = (dff - dff.shift(126)) >= 0.5
    if "신용 스프레드 긴장 (1년 평균 + 1표준편차 위)" in out and "실업률 상승 (3개월 평균이 1년 저점 +0.5%p)" in out:
        out["신용 긴장 또는 실업률 상승"] = out["신용 스프레드 긴장 (1년 평균 + 1표준편차 위)"] | \
            out["실업률 상승 (3개월 평균이 1년 저점 +0.5%p)"]
    return {k: v.fillna(False).astype(bool) for k, v in out.items()}


def backtest(px: pd.Series, risk: pd.Series | None, cash_rate: pd.Series) -> dict:
    r = px.pct_change().fillna(0.0)
    if risk is None:
        pos = pd.Series(1.0, index=px.index)
    else:
        pos = (~risk.reindex(px.index).fillna(False)).astype(float).shift(1).fillna(1.0)
    cr = (cash_rate.reindex(px.index).ffill().fillna(0.0) / 100) / 252
    trade = pos.diff().abs().fillna(0.0) * COST
    d = r * pos + cr * (1 - pos) - trade
    eq = (1 + d).cumprod()
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 0.5)
    return {"mult": float(eq.iloc[-1]), "cagr": (eq.iloc[-1] ** (1 / yrs) - 1) * 100,
            "mdd": float((eq / eq.cummax() - 1).min() * 100), "inv": float(pos.mean() * 100),
            "switch": int((pos.diff().abs() > 0).sum())}


def run(log=print) -> Path:
    t0 = time.time()
    if not fred.collected().get("files"):
        raise SystemExit("FRED 자료가 없습니다. 연구 탭 'FRED 경제 지표 받기'를 먼저 돌려 주세요.")
    log("시세 받는 중 (야후)…")
    us = load_us()
    assets = {"S&P500 (VOO)": us["SP500"], "나스닥100 (QQQ)": us["NDX100"], "QLD (2배)": us["QLD"]}
    try:
        assets["비트코인"] = weekdays(load_coins()["BTC"])
    except Exception:  # noqa: BLE001
        pass
    dff = fred.load("DFF", known=True)
    cash = dff if dff is not None else pd.Series(dtype=float)
    L = [f"경제 지표로 위험한 때 피하기 · {dt.date.today()} · FRED {fred.collected().get('updated', '')}",
         "신호가 켜진 동안 현금(기준금리 이자), 꺼지면 다시 보유 · 신호는 발표되어 알 수 있었던 날 기준 · 사고팔 때 0.1%", ""]
    L.append("1부. 규칙별 성적 (배수 · 연 수익 · 최대 낙폭 · 들고 있던 비율 · 사고판 횟수)")
    for name, px in assets.items():
        px = px[px.index >= "1990-01-01"].dropna()
        sig = signals(px.index)
        L += ["", f"  ■ {name} · {px.index[0].date()}~"]
        rows = [("그냥 보유", None)] + list(sig.items())
        for label, risk in rows:
            r = backtest(px, risk, cash)
            L.append(f"    {label:<38} {r['mult']:>7.1f}배 · 연 {r['cagr']:+5.1f}% · 낙폭 {r['mdd']:5.0f}% · "
                     f"투자 {r['inv']:3.0f}% · {r['switch']:,}번")
        own = px < px.rolling(200).mean()
        r = backtest(px, own, cash)
        L.append(f"    {'(비교) 자기 200일선 아래면 현금':<38} {r['mult']:>7.1f}배 · 연 {r['cagr']:+5.1f}% · 낙폭 {r['mdd']:5.0f}% · "
                 f"투자 {r['inv']:3.0f}% · {r['switch']:,}번")

    # 2부
    sp = us["SP500"][us["SP500"].index >= "1990-01-01"]
    sig = signals(sp.index)
    f6 = sp.shift(-126) / sp - 1
    f12 = sp.shift(-252) / sp - 1
    L += ["", "2부. 신호가 새로 켜진 날부터 S&P500 (6개월 뒤 · 1년 뒤, 평균과 켜진 횟수)",
          f"  아무 날이나: 6개월 {f6.mean() * 100:+.1f}% · 1년 {f12.mean() * 100:+.1f}%"]
    for label, s in sig.items():
        onset = s & ~s.shift(1, fill_value=False)
        # 20거래일 안에 다시 켜진 건 같은 사건으로
        days = list(s.index[onset])
        ev, prev = [], None
        for d in days:
            if prev is None or (d - prev).days > 60:
                ev.append(d)
            prev = d
        a6 = f6.reindex(ev).dropna()
        a12 = f12.reindex(ev).dropna()
        L.append(f"  {label:<38} {len(ev):>3}번 · 6개월 {a6.mean() * 100 if len(a6) else float('nan'):+.1f}% · "
                 f"1년 {a12.mean() * 100 if len(a12) else float('nan'):+.1f}% · 켜진 날 비율 {s.mean() * 100:.0f}%")
        L.append("      켜진 때: " + ", ".join(d.strftime("%Y-%m") for d in ev[-8:]))

    # 3부
    L += ["", "3부. 지금"]
    for sid, info in fred.SERIES.items():
        s = fred.load(sid)
        if s is None or s.empty:
            continue
        L.append(f"  {info['name']:<28} {s.iloc[-1]:,.2f}{info['unit']} ({s.index[-1].date()})")
    now = {k: bool(v.iloc[-1]) for k, v in signals(pd.DatetimeIndex([pd.Timestamp(dt.date.today())])).items()}
    L.append("  지금 켜진 신호: " + (", ".join(k for k, v in now.items() if v) or "없음"))
    L += ["", "읽는 법: 규칙이 '그냥 보유'보다 연 수익이 비슷하거나 높으면서 낙폭이 확실히 작아야 쓸 만함. 사고판 횟수가 많으면",
          "세금·실수 비용이 커진다. 2부에서 신호가 켜진 뒤 수익이 '아무 날'보다 낮아야 그 신호가 위험을 미리 알려준 것.",
          f"소요 {round((time.time() - t0) / 60, 1)}분"]
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"macro-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
