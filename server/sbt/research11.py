"""미국 지수 투자 검증: VOO 매일 적립, SPY·QQQ 월적립, 라오어 무한매수법(TQQQ).

  python -m sbt.data.cli us-test

데이터(야후): SPY·QQQ·TQQQ 수정주가(배당 재투자), 그 전 기간은 지수(^GSPC·^NDX, 배당 없음)를 이어 붙인다.
TQQQ 상장(2010-02) 전은 나스닥100 하루 등락 × 3 에서 연 비용을 뺀 '가짜 TQQQ' 로 이어 붙인다.
환율은 원/달러(KRW=X, 2003-12 부터).

1부. VOO 매일 1.5만원 적립 (VOO 상장 전은 SPY 로 대신): 2010~2025년 각 해 1월 첫 거래일 시작
     → 10년 뒤(있으면)와 지금. 원화 기준(환율 반영)과 달러 기준.
2부. 매달 100만원 적립: S&P500(SPY) vs 나스닥100(QQQ) vs TQQQ, 시작 해 여러 개.
3부. 무한매수법(단순화 V1): 1억원으로 시작, 40분할, 매일 LOC 매수(절반은 평단 이하일 때, 절반은 평단+10% 이하일 때),
     평단 +10% 지정가 전량 매도 후 다시 시작, 40회 다 쓰면 1/4 손절(쿼터 손절).
     같은 돈으로 QQQ·TQQQ 그냥 들고 있기와 비교.
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import config

FEE = 0.0007  # 미국 주식 매매 수수료 (온라인, 대략)
TQQQ_COST = 0.035  # 가짜 TQQQ 연 비용: 운용보수 0.95% + 레버리지 자금 비용(대략)


def _yf(ticker: str, start: str = "1985-01-01") -> pd.Series:
    import yfinance as yf

    df = yf.download(ticker, start=start, auto_adjust=True, progress=False, threads=False)
    if df is None or df.empty:
        return pd.Series(dtype=float)
    c = df["Close"]
    if isinstance(c, pd.DataFrame):
        c = c.iloc[:, 0]
    c.index = pd.to_datetime(c.index).tz_localize(None).normalize()
    return c.dropna()


def splice(new: pd.Series, old: pd.Series) -> pd.Series:
    """old 를 new 가 시작하는 날의 값에 맞춰 이어 붙인다."""
    if new.empty:
        return old
    base = old.asof(new.index[0])  # 이어 붙이는 날(또는 그 직전)의 옛 값
    old = old[old.index < new.index[0]]
    if old.empty or not np.isfinite(base):
        return new
    return pd.concat([old * (new.iloc[0] / base), new])


def synth_lev(base: pd.Series, lev: float = 3.0, cost: float = TQQQ_COST) -> pd.Series:
    r = base.pct_change().fillna(0.0)
    daily = lev * r - cost / 252
    return (1 + daily).cumprod() * 100


def load(cache: Path | None = None) -> dict[str, pd.Series]:
    """시세 받기 (하루 한 번 캐시)."""
    cache = cache or config.DATA_DIR / "us_etf.parquet"
    if cache.exists() and dt.date.fromtimestamp(cache.stat().st_mtime) == dt.date.today():
        df = pd.read_parquet(cache)
        return {c: df[c].dropna() for c in df.columns}
    spx, ndx = _yf("^GSPC"), _yf("^NDX")
    spy, qqq, tqqq, voo = _yf("SPY"), _yf("QQQ"), _yf("TQQQ"), _yf("VOO")
    fx = _yf("KRW=X", "2003-01-01")
    s = {
        "SP500": splice(voo, splice(spy, spx)),  # VOO ← SPY ← S&P500 지수
        "NDX100": splice(qqq, ndx),  # QQQ ← 나스닥100 지수
        "TQQQ": splice(tqqq, synth_lev(ndx)),  # TQQQ ← 가짜 TQQQ
        "KRW": fx[(fx > 500) & (fx < 3000)],  # 이상값 제거
        "_tqqq_real": tqqq,
        "_tqqq_fake": synth_lev(ndx),
    }
    pd.DataFrame(s).to_parquet(cache)
    return s


# ---------------------------------------------------------------- 적립식
def dca(price: pd.Series, fx: pd.Series | None, start, end, amount_krw: float, every: str) -> dict:
    """원화로 정해진 금액을 every('day' = 미국 거래일마다, 'month' = 달마다 첫 거래일)에 산다.
    fx 가 None 이면 달러 기준(원화 금액을 고정 환율로 본 것과 같음)."""
    p = price[(price.index >= start) & (price.index <= end)]
    if p.empty:
        return {}
    if fx is not None:
        f = fx.reindex(p.index, method="ffill")
        if f.isna().any():
            return {}
    else:
        f = pd.Series(1.0, index=p.index)
    if every == "day":
        buy_days = p.index
    else:
        buy_days = p.groupby([p.index.year, p.index.month]).head(1).index
    units = 0.0
    paid = 0.0
    worst = 0.0  # 원금 대비 가장 많이 깨졌던 비율
    vals = []
    buys = set(buy_days)
    for d in p.index:
        if d in buys:
            units += amount_krw * (1 - FEE) / (f[d] * p[d])
            paid += amount_krw
        v = units * p[d] * f[d]
        vals.append(v)
        if paid:
            worst = min(worst, v / paid - 1)
    v = pd.Series(vals, index=p.index)
    peak = v.cummax()
    return {"paid": paid, "value": float(v.iloc[-1]), "ret": (v.iloc[-1] / paid - 1) * 100, "worst": worst * 100,
            "mdd": float((v / peak - 1).min() * 100), "irr": _irr(p.index, buys, amount_krw, float(v.iloc[-1]))}


def _irr(days, buys, amt, final) -> float | None:
    """돈을 넣은 날짜별 연 수익률(돈 가중)."""
    t_end = days[-1]
    flows = [((t_end - d).days / 365.25, amt) for d in days if d in buys]
    if not flows:
        return None
    lo, hi = -0.99, 3.0
    for _ in range(80):
        mid = (lo + hi) / 2
        fv = sum(a * (1 + mid) ** t for t, a in flows)
        lo, hi = (mid, hi) if fv < final else (lo, mid)
    return lo * 100


# ---------------------------------------------------------------- 무한매수법
def infinite(px: pd.Series, start, end, capital: float, parts: int = 40, target: float = 0.10,
             big: float = 0.10, quarter: bool = True) -> dict:
    """단순화한 무한매수법(V1 방식). 종가만 있어서 '지정가 매도'는 그날 종가가 아니라 고가를 모르므로
    종가가 목표가 이상이면 목표가에 판 것으로 본다(보수적: 장중에만 닿은 날은 못 판 것으로)."""
    p = px[(px.index >= start) & (px.index <= end)]
    cash, units, cost = capital, 0.0, 0.0
    unit_amt = capital / parts
    used = 0.0  # 이번 회차에 쓴 분할 수
    cycles = quarters = 0
    eq = []
    for d, c in p.items():
        avg = cost / units if units else None
        # 매도: 평단 +10% (종가 기준으로 확인)
        if units and c >= avg * (1 + target):
            cash += units * avg * (1 + target) * (1 - FEE)
            units = cost = 0.0
            cycles += 1
            unit_amt = cash / parts  # 다음 회차는 늘어난 돈으로 다시 40분할
            used = 0.0
        else:
            if units and used >= parts - 1e-9:
                if quarter:  # 40회 다 씀 → 1/4 손절
                    q = units / 4
                    cash += q * c * (1 - FEE)
                    cost *= 0.75
                    units -= q
                    used = parts * 0.75
                    quarters += 1
            if units and cash < unit_amt / 2:
                used = parts  # 살 돈이 바닥 → 다음 날 쿼터 손절로 현금을 만든다
            if used < parts - 1e-9:
                half = unit_amt / 2
                buys = []
                if not units:
                    buys = [unit_amt]  # 첫날: 1회분
                else:
                    if c <= avg:  # LOC 평단
                        buys.append(half)
                    if c <= avg * (1 + big):  # LOC 평단+10% (큰 수)
                        buys.append(half)
                for amt in buys:
                    amt = min(amt, cash)
                    if amt <= 0:
                        continue
                    n = amt * (1 - FEE) / c
                    units += n
                    cost += amt
                    cash -= amt
                    used += amt / unit_amt
        eq.append(cash + units * c)
    e = pd.Series(eq, index=p.index)
    yrs = max((p.index[-1] - p.index[0]).days / 365.25, 0.1)
    return {"final": float(e.iloc[-1]), "cagr": ((e.iloc[-1] / capital) ** (1 / yrs) - 1) * 100,
            "mdd": float((e / e.cummax() - 1).min() * 100), "cycles": cycles, "quarters": quarters,
            "invested": float(units * p.iloc[-1] / e.iloc[-1] * 100) if e.iloc[-1] else 0.0}


def hold(px: pd.Series, start, end, capital: float) -> dict:
    p = px[(px.index >= start) & (px.index <= end)]
    e = capital * (1 - FEE) * p / p.iloc[0]
    yrs = max((p.index[-1] - p.index[0]).days / 365.25, 0.1)
    return {"final": float(e.iloc[-1]), "cagr": ((e.iloc[-1] / capital) ** (1 / yrs) - 1) * 100,
            "mdd": float((e / e.cummax() - 1).min() * 100)}


def run(log=print) -> Path:
    t0 = time.time()
    log("시세 받는 중 (야후)…")
    s = load()
    sp, nq, tq, fx = s["SP500"], s["NDX100"], s["TQQQ"], s["KRW"]
    last = min(sp.index[-1], nq.index[-1])
    L = [f"미국 지수 투자 검증 · {dt.date.today()} · 데이터 ~{last.date()} · 수수료 {FEE * 100:.2f}% · 배당 재투자(ETF 수정주가)"]
    # 가짜 TQQQ 확인
    real, fake = s["_tqqq_real"], s["_tqqq_fake"]
    both = real.index.intersection(fake.index)
    if len(both) > 250:
        rr = real[both] / real[both].iloc[0]
        ff = fake[both] / fake[both].iloc[0]
        L.append(f"가짜 TQQQ 확인: {both[0].date()}~ 실제 TQQQ {rr.iloc[-1]:.1f}배 vs 가짜 {ff.iloc[-1]:.1f}배 "
                 f"(하루 등락 상관 {real[both].pct_change().corr(fake[both].pct_change()):.3f})")

    # 1부
    L += ["", "1부. VOO 매일 1.5만원 적립 (미국 거래일마다, 각 해 1월 첫 거래일 시작)",
          "  시작   | 10년 뒤: 원금 → 평가액 (수익률, 연) | 지금까지: 원금 → 평가액 (수익률, 연) | 원금 대비 최악 | 달러 기준 지금"]
    for y in range(2010, 2026):
        start = pd.Timestamp(f"{y}-01-01")
        ten = dca(sp, fx, start, pd.Timestamp(f"{y + 10}-01-01") - pd.Timedelta(days=1), 15_000, "day") \
            if y + 10 <= last.year else {}
        now = dca(sp, fx, start, last, 15_000, "day")
        usd = dca(sp, None, start, last, 15_000, "day")
        if not now:
            continue
        t_s = (f"{ten['paid'] / 1e4:,.0f}만 → {ten['value'] / 1e4:,.0f}만 ({ten['ret']:+.0f}%, 연 {ten['irr']:+.1f}%)"
               if ten else "아직 10년 안 됨")
        L.append(f"  {y}  | {t_s:<34} | {now['paid'] / 1e4:,.0f}만 → {now['value'] / 1e4:,.0f}만 "
                 f"({now['ret']:+.0f}%, 연 {now['irr']:+.1f}%) | {now['worst']:+.0f}% | {usd['ret']:+.0f}%")
    L.append("  원금 대비 최악 = 적립하는 동안 평가액이 넣은 돈보다 가장 많이 밑돌았을 때 (0% 이면 원금이 깨진 적 없음)")

    # 2부
    L += ["", "2부. 매달 100만원 적립 (원화 기준, 지금까지) · S&P500(VOO) / 나스닥100(QQQ) / TQQQ"]
    for y in (2004, 2007, 2010, 2015, 2020, 2022):
        start = pd.Timestamp(f"{y}-01-01")
        row = []
        for name, px in (("S&P500", sp), ("QQQ", nq), ("TQQQ", tq)):
            r = dca(px, fx, start, last, 1_000_000, "month")
            if r:
                row.append(f"{name} {r['value'] / 1e8:,.2f}억(연 {r['irr']:+.1f}%, 최악 {r['worst']:+.0f}%)")
        paid = dca(sp, fx, start, last, 1_000_000, "month").get("paid", 0)
        L.append(f"  {y}~ 원금 {paid / 1e8:,.2f}억 → " + " · ".join(row))
    L.append("  (2004 전은 환율 데이터가 없어 뺌. 최악 = 원금 대비 가장 많이 깨졌을 때)")

    # 3부
    L += ["", "3부. 무한매수법(단순화 V1) vs 그냥 들고 있기 · 1억원(달러 기준) 한 번에 시작 · 지금까지"]
    for y in (2000, 2003, 2008, 2010, 2015, 2020, 2022):
        start = pd.Timestamp(f"{y}-01-01")
        r = infinite(tq, start, last, 1e8)
        h1, h3 = hold(nq, start, last, 1e8), hold(tq, start, last, 1e8)
        L.append(f"  {y}~ 무한매수 {r['final'] / 1e8:,.2f}억 (연 {r['cagr']:+.1f}%, 낙폭 {r['mdd']:.0f}%, "
                 f"익절 {r['cycles']}번, 쿼터손절 {r['quarters']}번) | QQQ 보유 {h1['final'] / 1e8:,.2f}억 "
                 f"(연 {h1['cagr']:+.1f}%, 낙폭 {h1['mdd']:.0f}%) | TQQQ 보유 {h3['final'] / 1e8:,.2f}억 "
                 f"(연 {h3['cagr']:+.1f}%, 낙폭 {h3['mdd']:.0f}%)")
    L += ["  무한매수법 가정: 40분할 · 매일 종가에 '평단 이하면 0.5회분 + 평단+10% 이하면 0.5회분' · 종가가 평단+10% 이상이면 "
          "전량 매도 후 늘어난 돈으로 다시 40분할 · 40회 다 쓰면 1/4 손절. 실제 버전(V2.2·V3)은 세부가 다르다.",
          "  2010-02 전 TQQQ 는 나스닥100 × 3 으로 만든 가짜 (위 '가짜 TQQQ 확인' 줄 참고).",
          f"소요 {round((time.time() - t0) / 60, 1)}분"]
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"us-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
