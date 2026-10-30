"""QLD 5천원 + 비중 상한 규칙 검증.

  python -m sbt.data.cli qld-rule

매일(미국 거래일마다) VOO 1.5만 + QQQ 1만 + 5천원을 넣는다. 5천원을 어떻게 쓰느냐만 다르다.
  A. QLD 없이: 5천원도 VOO
  B. QLD 5천원 계속 (상한 없음)
  C. 규칙: 해마다 1월 첫 거래일에 QLD 비중을 보고, 25% 넘으면 그해 5천원은 VOO 로 (안 넘으면 QLD)
  D. 규칙 + 팔아서 맞추기: C 와 같은데, 25% 넘으면 넘는 만큼 QLD 를 팔아 VOO 로 옮긴다 (세금은 빼고 계산)
SCHD 는 상장(2011) 전 데이터가 없어 뺀다. 환율 데이터(2003-12~) 전 시작은 달러 기준.
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import config
from .research11 import FEE
from .research12 import _won, load

CAP = 0.25
DAILY = {"voo": 15_000, "qqq": 10_000}
EXTRA = 5_000
PLANS = [("A", "QLD 없이 (5천도 VOO)"), ("B", "QLD 5천 계속"), ("C", "QLD 5천, 25% 넘으면 그해 VOO 로"),
         ("D", "C + 넘는 만큼 팔아 VOO 로")]


DIP = ((1.0, 1), (0.7, 2), (0.0, 4))  # QLD 가 내 평단의 몇 배 이상이면 5천 × 몇 배 (평단 이상 1배, -30%까지 2배, 그 아래 4배)
DIP_PLANS = [("A", "QLD 없이 (5천도 VOO)"), ("D", "QLD 5천 + 25% 넘으면 팔아 VOO 로 (지금 추천)"),
             ("E", "D + 평단 아래면 QLD 1만, 평단 -30% 아래면 2만"), ("F", "E 와 같은 날 같은 추가 금액을 VOO 로")]


def simulate(px: pd.DataFrame, fx: pd.Series | None, start, end, plan: str, cap: float = CAP) -> dict:
    """px 열: voo, qqq, qld (달러 가격). 원화로 정해진 금액을 매일 산다.
    E 는 D 규칙에 더해 QLD 가 평단보다 싸면 더 넣는다. F 는 E 와 같은 날 같은 추가 금액을 VOO 에 넣는다."""
    p = px[(px.index >= start) & (px.index <= end)].dropna()
    if p.empty:
        return {}
    sched = simulate(px, fx, start, end, "E", cap).get("mults") if plan == "F" else None
    f = fx.reindex(p.index, method="ffill").to_numpy() if fx is not None else np.ones(len(p))
    if np.isnan(f).any():
        return {}
    v, q, l = (p[c].to_numpy() for c in ("voo", "qqq", "qld"))
    units = np.zeros(3)  # voo, qqq, qld
    qld_cost = 0.0  # 지금 가진 QLD 의 매수 원금 (평단 계산용)
    paid = 0.0
    worst = 0.0
    to_qld = plan != "A"
    rebalance = plan in ("C", "D", "E", "F")
    vals = np.empty(len(p))
    flows = np.empty(len(p))
    mults = np.ones(len(p), dtype=int)
    extra_by_year: dict[int, float] = {}
    shares = []  # 해마다 1월 확인 때 QLD 비중
    years = p.index.year.to_numpy()
    for i in range(len(p)):
        price = np.array([v[i], q[i], l[i]]) * f[i]  # 원화 가격
        if rebalance and i > 0 and years[i] != years[i - 1]:
            val = units * price
            tot = val.sum()
            share = val[2] / tot if tot > 0 else 0.0
            shares.append(share)
            if share > cap:
                to_qld = False
                if plan != "C":
                    move = val[2] - cap * tot
                    qld_cost *= 1 - move / val[2]
                    units[2] -= move / price[2]
                    units[0] += move * (1 - FEE) ** 2 / price[0]
            else:
                to_qld = True
        units[0] += DAILY["voo"] * (1 - FEE) / price[0]
        units[1] += DAILY["qqq"] * (1 - FEE) / price[1]
        mult = 1
        if plan == "E" and to_qld and units[2] > 0:
            ratio = price[2] / (qld_cost / units[2])
            mult = next(m for lo, m in DIP if ratio >= lo)
        elif sched is not None:
            mult = int(sched[i])
        mults[i] = mult
        amt = EXTRA * mult
        if to_qld or plan == "F":
            q_amt = amt if plan != "F" else (EXTRA if to_qld else 0)
            units[2] += q_amt * (1 - FEE) / price[2]
            qld_cost += q_amt
            units[0] += (amt - q_amt) * (1 - FEE) / price[0]
        else:
            units[0] += amt * (1 - FEE) / price[0]
        if amt > EXTRA:
            extra_by_year[years[i]] = extra_by_year.get(years[i], 0.0) + amt - EXTRA
        today = DAILY["voo"] + DAILY["qqq"] + amt
        paid += today
        flows[i] = today
        vals[i] = (units * price).sum()
        worst = min(worst, vals[i] / paid - 1)
    s = pd.Series(vals, index=p.index)
    last = units * np.array([v[-1], q[-1], l[-1]]) * f[-1]
    return {"paid": paid, "value": float(vals[-1]), "ret": (vals[-1] / paid - 1) * 100, "worst": worst * 100,
            "mdd": float((s / s.cummax() - 1).min() * 100), "share": last[2] / last.sum() * 100,
            "max_share": max(shares) * 100 if shares else None,
            "irr": _irr(p.index, flows, float(vals[-1])),
            "extra": sum(extra_by_year.values()), "extra_year": max(extra_by_year.values(), default=0.0),
            "mults": mults}


def _irr(days: pd.DatetimeIndex, flows: np.ndarray, final: float) -> float:
    """매일 넣은 돈 기준 연 수익률 (돈 가중)."""
    t = (days[-1] - days).days.to_numpy() / 365.25
    lo, hi = -0.99, 3.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if (flows * (1 + mid) ** t).sum() < final else (lo, mid)
    return lo * 100


def run(log=print) -> Path:
    t0 = time.time()
    log("시세 받는 중 (야후)…")
    s = load()
    px = pd.DataFrame({"voo": s["SP500"], "qqq": s["NDX100"], "qld": s["QLD"]}).dropna()
    fx = s["KRW"]
    last = px.index[-1]
    L = [f"QLD 5천원 + 비중 상한 규칙 · {dt.date.today()} · 데이터 ~{last.date()} · 수수료 {FEE * 100:.2f}% · 세금 제외",
         f"매일 VOO 1.5만 + QQQ 1만 + 5천 (하루 3만원) · 상한 {CAP * 100:.0f}% · 해마다 1월 첫 거래일에 확인",
         "  " + " · ".join(f"{k}. {name}" for k, name in PLANS), ""]
    for title, ten in (("10년 뒤", True), ("지금까지", False)):
        L += [f"■ {title}: 평가액 (원금 대비 수익률) / 평가액 최대 낙폭 · 원금 대비 최악 / 끝날 때 QLD 비중",
              "  ($ = 환율 데이터 전이라 달러 기준)"]
        for y in (range(1995, last.year) if ten else (1995, 1998, 2000, 2003, 2007, 2010, 2015, 2018, 2020, 2022)):
            start = pd.Timestamp(f"{y}-01-01")
            end = pd.Timestamp(f"{y + 10}-01-01") - pd.Timedelta(days=1) if ten else last
            if end > last:
                continue
            f = fx if start >= fx.index[0] else None
            rs = {k: simulate(px, f, start, end, k) for k, _ in PLANS}
            if not all(rs.values()):
                continue
            a = rs["A"]
            L.append(f" {'$' if f is None else ' '}{y} 원금 {_won(a['paid'])}")
            for k, _ in PLANS:
                r = rs[k]
                diff = (r["value"] / a["value"] - 1) * 100
                L.append(f"     {k} {_won(r['value']):>9} ({r['ret']:+5.0f}%) {'' if k == 'A' else f'A 대비 {diff:+4.0f}%':<12}"
                         f" 낙폭 {r['mdd']:4.0f}% · 최악 {r['worst']:+4.0f}%"
                         + (f" · QLD {r['share']:.0f}%" if k != "A" else "")
                         + (f" (1월 확인 때 최고 {r['max_share']:.0f}%)" if r.get("max_share") is not None else ""))
        L.append("")
    L += ["읽는 법: A 대비 = 같은 돈을 QLD 없이 넣었을 때보다 몇 % 많거나 적은지 · 낙폭 = 쌓인 평가액이 가장 좋았을 때에서 "
          "가장 많이 빠졌을 때 · 최악 = 넣은 돈보다 가장 많이 밑돌았을 때",
          "D 는 판 차익에 세금(해외주식 연 250만 넘으면 22%)이 붙을 수 있는데 여기선 뺐다.",
          f"소요 {round((time.time() - t0) / 60, 1)}분"]
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"qld-rule-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path


def run_dip(log=print) -> Path:
    """QLD 가 내 평단보다 떨어지면 더 사기 (qld-dip)."""
    t0 = time.time()
    log("시세 받는 중 (야후)…")
    s = load()
    px = pd.DataFrame({"voo": s["SP500"], "qqq": s["NDX100"], "qld": s["QLD"]}).dropna()
    fx = s["KRW"]
    last = px.index[-1]
    L = [f"QLD 폭락 때 더 사기 · {dt.date.today()} · 데이터 ~{last.date()} · 수수료 {FEE * 100:.2f}% · 세금 제외",
         "기본: 매일 VOO 1.5만 + QQQ 1만 + QLD 5천, 해마다 1월 QLD 25% 넘으면 넘는 만큼 팔아 VOO 로 (D)",
         "더 사기: 그날 QLD 가격이 내 QLD 평단보다 낮으면 5천 대신 1만, 평단 -30% 보다 낮으면 2만",
         "  " + " · ".join(f"{k}. {name}" for k, name in DIP_PLANS),
         "  원금이 계획마다 달라서 '연' = 넣은 날짜·금액을 반영한 연 수익률(돈 가중)로 비교한다", ""]
    for title, ten in (("10년 뒤", True), ("지금까지", False)):
        L += [f"■ {title}: 평가액 / 원금 (연 수익률) · 평가액 최대 낙폭 · 원금 대비 최악 · 추가로 넣은 돈 합계 (한 해 최대)",
              "  ($ = 환율 데이터 전이라 달러 기준)"]
        years = range(1995, last.year) if ten else (1995, 1998, 2000, 2003, 2007, 2010, 2015, 2018, 2020, 2022)
        for y in years:
            start = pd.Timestamp(f"{y}-01-01")
            end = pd.Timestamp(f"{y + 10}-01-01") - pd.Timedelta(days=1) if ten else last
            if end > last:
                continue
            f = fx if start >= fx.index[0] else None
            rs = {k: simulate(px, f, start, end, k) for k, _ in DIP_PLANS}
            if not all(rs.values()):
                continue
            L.append(f" {'$' if f is None else ' '}{y}")
            for k, _ in DIP_PLANS:
                r = rs[k]
                L.append(f"     {k} {_won(r['value']):>9} / {_won(r['paid']):>7} (연 {r['irr']:+5.1f}%) · 낙폭 {r['mdd']:4.0f}% · "
                         f"최악 {r['worst']:+4.0f}%"
                         + (f" · 추가 {_won(r['extra'])} (한 해 최대 {_won(r['extra_year'])})" if r["extra"] else ""))
        L.append("")
    L += ["읽는 법: E 가 D 보다 연 수익률이 높으면 '싸게 더 사기'가 효과 있었던 것. F 보다도 높아야 QLD 에 넣은 게 의미 있음",
          "(F 는 같은 날 같은 돈을 VOO 에 넣은 것). 추가 합계 = 폭락 동안 평소보다 더 넣은 돈 = 미리 준비해 둬야 하는 돈.",
          f"소요 {round((time.time() - t0) / 60, 1)}분"]
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"qld-dip-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
