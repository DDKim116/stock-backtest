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


def simulate(px: pd.DataFrame, fx: pd.Series | None, start, end, plan: str, cap: float = CAP) -> dict:
    """px 열: voo, qqq, qld (달러 가격). 원화로 정해진 금액을 매일 산다."""
    p = px[(px.index >= start) & (px.index <= end)].dropna()
    if p.empty:
        return {}
    f = fx.reindex(p.index, method="ffill").to_numpy() if fx is not None else np.ones(len(p))
    if np.isnan(f).any():
        return {}
    v, q, l = (p[c].to_numpy() for c in ("voo", "qqq", "qld"))
    units = np.zeros(3)  # voo, qqq, qld
    paid = 0.0
    worst = 0.0
    to_qld = plan != "A"
    vals = np.empty(len(p))
    shares = []  # 해마다 1월 확인 때 QLD 비중
    years = p.index.year.to_numpy()
    for i in range(len(p)):
        price = np.array([v[i], q[i], l[i]]) * f[i]  # 원화 가격
        if plan in ("C", "D") and i > 0 and years[i] != years[i - 1]:
            val = units * price
            tot = val.sum()
            share = val[2] / tot if tot > 0 else 0.0
            shares.append(share)
            if share > cap:
                to_qld = False
                if plan == "D":
                    move = val[2] - cap * tot
                    units[2] -= move / price[2]
                    units[0] += move * (1 - FEE) ** 2 / price[0]
            else:
                to_qld = True
        units[0] += DAILY["voo"] * (1 - FEE) / price[0]
        units[1] += DAILY["qqq"] * (1 - FEE) / price[1]
        k = 2 if to_qld else 0
        units[k] += EXTRA * (1 - FEE) / price[k]
        paid += DAILY["voo"] + DAILY["qqq"] + EXTRA
        vals[i] = (units * price).sum()
        worst = min(worst, vals[i] / paid - 1)
    s = pd.Series(vals, index=p.index)
    last = units * np.array([v[-1], q[-1], l[-1]]) * f[-1]
    return {"paid": paid, "value": float(vals[-1]), "ret": (vals[-1] / paid - 1) * 100, "worst": worst * 100,
            "mdd": float((s / s.cummax() - 1).min() * 100), "share": last[2] / last.sum() * 100,
            "max_share": max(shares) * 100 if shares else None}


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
