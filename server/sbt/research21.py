"""비트코인 더 사기 규칙 · 미국 주식과 비트코인의 관계.

  python -m sbt.data.cli btc-dip

1부. 매일(평일) 비트코인 3천원 기본에 '쌀 때 더 사기' 규칙을 붙이면?
  A. 기본: 매일 3천원
  B. 내 평단 아래면 6천, 평단 -30% 아래면 1.2만 (QLD 와 같은 규칙)
  C. 고점 대비 -50% 아래면 6천, -70% 아래면 1.2만 (가격이 크게 빠졌을 때)
  B'·C'. B·C 와 같은 날 같은 추가 금액을 VOO 에 (공정 비교)
  원금이 달라서 넣은 날짜·금액을 반영한 연 수익률(돈 가중)로 비교. 추가로 넣은 돈 합계·한 해 최대도.
2부. 미국 주식(S&P500 · 나스닥100)과 비트코인: 해마다 하루 등락 상관, 주식이 크게 빠진 날 비트코인,
  큰 하락장에서 비트코인, S&P 가 200일선 위·아래일 때 비트코인 성적.
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import config
from .research11 import FEE
from .research12 import _won
from .research12 import load as load_us
from .research13 import _irr
from .research20 import load as load_coins
from .research20 import weekdays

BASE = 3_000
RULES = [("A", "기본 매일 3천"), ("B", "평단 아래 6천 · -30% 아래 1.2만"), ("C", "고점 -50% 아래 6천 · -70% 아래 1.2만"),
         ("B'", "B 의 추가분을 VOO 로"), ("C'", "C 의 추가분을 VOO 로")]


def simulate(btc: pd.Series, voo: pd.Series, fx: pd.Series, start, end, rule: str) -> dict:
    """btc·voo 달러 가격(평일), 원화로 산다. B'·C' 는 B·C 의 날짜별 배수를 그대로 쓰고 추가분만 VOO."""
    idx = btc.index[(btc.index >= start) & (btc.index <= end)]
    if len(idx) < 20:
        return {}
    b = btc.reindex(idx).to_numpy()
    v = voo.reindex(idx, method="ffill").to_numpy()
    f = fx.reindex(idx, method="ffill").to_numpy()
    if np.isnan(f).any() or np.isnan(v).any():
        return {}
    peak = btc[btc.index <= end].cummax().reindex(idx).to_numpy()
    sched = simulate(btc, voo, fx, start, end, rule[0])["mults"] if rule.endswith("'") else None
    ub = uv = cost = paid = 0.0
    worst = 0.0
    flows, vals, mults = np.zeros(len(idx)), np.zeros(len(idx)), np.ones(len(idx), dtype=int)
    extra_y: dict[int, float] = {}
    for i in range(len(idx)):
        pb, pv = b[i] * f[i], v[i] * f[i]
        m = 1
        if sched is not None:
            m = int(sched[i])
        elif rule == "B" and ub > 0:
            r = pb / (cost / ub)
            m = 1 if r >= 1 else 2 if r >= 0.7 else 4
        elif rule == "C":
            dd = b[i] / peak[i]
            m = 1 if dd > 0.5 else 2 if dd > 0.3 else 4
        mults[i] = m
        amt = BASE * m
        to_btc = BASE if rule.endswith("'") else amt
        ub += to_btc * (1 - FEE) / pb
        cost += to_btc
        uv += (amt - to_btc) * (1 - FEE) / pv
        paid += amt
        flows[i] = amt
        if m > 1:
            extra_y[idx[i].year] = extra_y.get(idx[i].year, 0.0) + amt - BASE
        vals[i] = ub * pb + uv * pv
        worst = min(worst, vals[i] / paid - 1)
    s = pd.Series(vals, index=idx)
    return {"paid": paid, "value": float(vals[-1]), "irr": _irr(idx, flows, float(vals[-1])), "worst": worst * 100,
            "mdd": float((s / s.cummax() - 1).min() * 100), "extra": sum(extra_y.values()),
            "extra_y": max(extra_y.values(), default=0.0), "mults": mults}


def run(log=print) -> Path:
    t0 = time.time()
    log("시세 받는 중 (야후)…")
    btc = weekdays(load_coins()["BTC"])
    us = load_us()
    sp, nq, fx = us["SP500"], us["NDX100"], us["KRW"]
    last = btc.index[-1]
    L = [f"비트코인 더 사기 · 미국 주식과의 관계 · {dt.date.today()} · 데이터 ~{last.date()} · 평일마다 · 원화 기준 · 수수료 {FEE * 100:.2f}%",
         "  " + " · ".join(f"{k}. {n}" for k, n in RULES), ""]
    for title, ten in (("5년 뒤", True), ("지금까지", False)):
        L += [f"1부. {title}: 평가액 / 원금 (연 수익률, 돈 가중) · 원금 대비 최악 · 추가로 넣은 돈 (한 해 최대)"]
        for y in range(2015, last.year + (0 if ten else 1)):
            start = pd.Timestamp(f"{y}-01-01")
            end = pd.Timestamp(f"{y + 5}-01-01") - pd.Timedelta(days=1) if ten else last
            if end > last or start < btc.index[0]:
                continue
            rs = {k: simulate(btc, sp, fx, start, end, k) for k, _ in RULES}
            if not all(rs.values()):
                continue
            L.append(f"  {y}")
            for k, _ in RULES:
                r = rs[k]
                L.append(f"    {k:<2} {_won(r['value']):>8} / {_won(r['paid']):>7} (연 {r['irr']:+6.1f}%) · 최악 {r['worst']:+4.0f}%"
                         + (f" · 추가 {_won(r['extra'])} (한 해 최대 {_won(r['extra_y'])})" if r["extra"] else ""))
        L.append("")
    L.append("  읽는 법: B·C 가 A 보다 연 수익률이 높고, B'·C'(같은 돈을 VOO 에)보다도 높아야 '비트코인을 더 사는' 의미가 있음.")

    # 2부 -----------------------------------------------------------------
    log("관계 계산…")
    df = pd.DataFrame({"btc": btc, "sp": sp, "nq": nq}).dropna()
    r = df.pct_change().dropna()
    L += ["", "2부. 미국 주식과 비트코인 (평일 하루 등락 기준, 달러)",
          "  해마다 상관 (1 = 똑같이, 0 = 관계 없음, -1 = 반대로) · 베타 = S&P 가 1% 움직일 때 비트코인 평균 몇 %"]
    rows = []
    for y, g in r.groupby(r.index.year):
        if len(g) < 60:
            continue
        c_sp, c_nq = g["btc"].corr(g["sp"]), g["btc"].corr(g["nq"])
        beta = g["btc"].cov(g["sp"]) / g["sp"].var()
        rows.append(f"{y} S&P {c_sp:+.2f} · 나스닥 {c_nq:+.2f} · 베타 {beta:.1f}")
    for k in range(0, len(rows), 3):
        L.append("    " + "   |   ".join(rows[k:k + 3]))
    big = r[r["sp"] <= -0.02]
    up = r[r["sp"] >= 0.02]
    L += ["", f"  S&P 하루 -2% 이하인 날 ({len(big)}일): 비트코인 평균 {big['btc'].mean() * 100:+.1f}% · 같이 빠진 날 {(big['btc'] < 0).mean() * 100:.0f}%",
          f"  S&P 하루 +2% 이상인 날 ({len(up)}일): 비트코인 평균 {up['btc'].mean() * 100:+.1f}% · 같이 오른 날 {(up['btc'] > 0).mean() * 100:.0f}%"]
    L += ["", "  큰 하락장에서 (같은 기간, 가격 변화)"]
    for name, a, b in (("2018 하반기 금리 인상", "2018-09-20", "2018-12-24"), ("2020.2~3 코로나", "2020-02-19", "2020-03-23"),
                       ("2022 금리 급등", "2022-01-03", "2022-10-12"), ("2025 관세 충격", "2025-02-19", "2025-04-08")):
        seg = df[(df.index >= a) & (df.index <= b)]
        if len(seg) > 2:
            ch = seg.iloc[-1] / seg.iloc[0] - 1
            L.append(f"    {name:<16} S&P {ch['sp'] * 100:+.0f}% · 나스닥100 {ch['nq'] * 100:+.0f}% · 비트코인 {ch['btc'] * 100:+.0f}%")
    on = (sp > sp.rolling(200).mean()).reindex(r.index).shift(1).fillna(False).astype(bool)
    for label, m in (("S&P 200일선 위", on), ("S&P 200일선 아래", ~on)):
        g = r["btc"][m]
        L.append(f"  {label}일 때 비트코인 하루 평균 {g.mean() * 100:+.2f}% · 연으로 치면 약 {((1 + g.mean()) ** 252 - 1) * 100:+.0f}% "
                 f"({len(g)}일)")
    L += ["", "읽는 법: 상관이 0.3 넘으면 '주식과 꽤 같이 움직임'. 주식이 크게 빠질 때 같이 빠지면 분산 효과는 작다(피난처가 아님).",
          f"소요 {round((time.time() - t0) / 60, 1)}분"]
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"btc-dip-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
