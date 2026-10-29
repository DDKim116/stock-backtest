"""v2b 크기 정하기: 1회 금액을 고정할지, 계좌 비율로 할지. 5천만원으로 2010년부터.

  python -m sbt.data.cli size-test

- 고정: 1회 300만×하루 15 / 500만×10 (지금) / 700만×7 / 1,000만×5
- 비율: 그날 계좌(현금 + 보유 종목 매수금)의 7% / 10% / 15% / 20% 씩 (계좌가 커지면 같이 커진다)
같은 날은 거래대금 작은 순, 이미 가진 종목은 다시 사지 않고, 판 돈은 다음 거래일부터 다시 쓴다 (앱 자동 매매와 같은 규칙).
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import breadth, config
from .data.indices import attach as indices_attach
from .data.panel import Panel
from .engine.run import Runner
from .research import _family_spec
from .research7 import V2B, Leg

CAPITAL = 50_000_000
CUT = pd.Timestamp("2021-01-01")
CONFIGS: list[tuple] = [  # (이름, 고정 금액, 비율, 하루 최대, 1회 상한)
    ("고정 300만 × 하루 15", 3_000_000, None, 15),
    ("고정 500만 × 하루 10 (지금)", 5_000_000, None, 10),
    ("고정 700만 × 하루 7", 7_000_000, None, 7),
    ("고정 1,000만 × 하루 5", 10_000_000, None, 5),
    ("비율 7% × 하루 14", None, 0.07, 14),
    ("비율 10% × 하루 10", None, 0.10, 10),
    ("비율 15% × 하루 7", None, 0.15, 7),
    ("비율 20% × 하루 5", None, 0.20, 5),
    ("비율 10% × 10, 1회 최대 2천만 (추천)", None, 0.10, 10, 20_000_000),
    ("비율 10% × 10, 1회 최대 3천만", None, 0.10, 10, 30_000_000),
    ("비율 7% × 14, 1회 최대 2천만", None, 0.07, 14, 20_000_000),
]


def simulate(g: Leg, days: np.ndarray, capital: float, per: float | None, pct: float | None, daily: int,
             cap: float | None = None) -> dict:
    """하루씩 넘기며 사고판다. 평가는 실현 손익 기준(보유 종목은 매수금으로 친다)."""
    by_day: dict = {}
    for k in np.lexsort((np.nan_to_num(g.value, nan=np.inf), g.fill)):
        by_day.setdefault(g.fill[k], []).append(k)
    cash = capital
    open_: list[list] = []  # [청산일, 매수금, 수익률%, 코드, 끝남]
    held: set[str] = set()
    realized: list[tuple] = []  # (날짜, 손익)
    idle: list[float] = []
    taken = skipped = 0
    first = g.fill.min() if len(g.fill) else days[0]
    for d in days[days >= first]:
        keep = []
        for e, cost, r, code, done in open_:
            if done and e < d:
                back = cost * (1 + r / 100)
                cash += back
                held.discard(code)
                realized.append((e, back - cost))
            else:
                keep.append([e, cost, r, code, done])
        open_ = keep
        equity = cash + sum(x[1] for x in open_)
        n = 0
        for k in by_day.get(d, []):
            if n >= daily or g.code[k] in held:
                continue
            amt = per if per is not None else equity * pct
            if cap is not None:
                amt = min(amt, cap)
            if cash < amt:
                skipped += 1
                continue
            cash -= amt
            n += 1
            taken += 1
            held.add(g.code[k])
            open_.append([g.exit[k], amt, float(g.ret[k]), g.code[k], bool(g.done[k])])
        equity = cash + sum(x[1] for x in open_)
        idle.append(cash / equity if equity > 0 else 1.0)
    for e, cost, r, code, done in open_:
        if done:
            realized.append((e, cost * r / 100))
    final = capital + sum(p for _, p in realized)
    rs = pd.Series([p for _, p in realized], index=pd.DatetimeIndex([d for d, _ in realized])).sort_index()
    eq = capital + rs.cumsum()
    peak = eq.cummax()
    mdd = float((eq / peak - 1).min() * 100) if len(eq) else 0.0
    mdd_won = float((eq - peak).min()) if len(eq) else 0.0
    # 연속 손실: 청산 순서대로 이어진 손실 거래의 수와 합계
    streak = worst_n = 0
    run_sum = worst_sum = 0.0
    for p in rs.to_numpy():
        if p < 0:
            streak += 1
            run_sum += p
            if run_sum < worst_sum:
                worst_sum, worst_n = run_sum, streak
        else:
            streak, run_sum = 0, 0.0
    years = max((rs.index[-1] - rs.index[0]).days / 365.25, 0.5) if len(rs) else 1
    ye = eq.groupby(eq.index.year).last()
    start = pd.Series({ye.index[0]: capital}) if len(ye) else pd.Series(dtype=float)
    base = pd.concat([start, ye.iloc[:-1].set_axis(ye.index[1:])])
    by_year = (ye / base - 1) * 100
    pre = eq[eq.index < CUT]
    e0 = float(pre.iloc[-1]) if len(pre) else capital
    oos_years = max((rs.index[-1] - CUT).days / 365.25, 0.5) if len(rs) else 1
    return {"final": final, "cagr": ((final / capital) ** (1 / years) - 1) * 100,
            "oos_cagr": ((final / e0) ** (1 / oos_years) - 1) * 100 if e0 > 0 else None,
            "mdd": mdd, "mdd_won": mdd_won, "worst_n": worst_n, "worst_sum": worst_sum,
            "idle": float(np.mean(idle) * 100) if idle else 100.0, "taken": taken, "skipped": skipped,
            "by_year": by_year}


def run(dataset: str = "kr", log=print) -> Path:
    t0 = time.time()
    panel = Panel.load(config.dataset_dir(dataset) / "prices.parquet")
    if not indices_attach(panel, dataset):
        raise SystemExit("지수 데이터가 없습니다.")
    breadth.attach(panel)
    runner = Runner(panel, {"name": dataset, "markets": ["KOSPI", "KOSDAQ"], "currency": "KRW"})
    log("v2b 신호 계산…")
    g = Leg("v2b", runner, _family_spec(V2B, ["코스피", "코스닥"]), 0, 0)
    days = np.unique(panel.dates)
    L = [f"v2b 크기 정하기 · {dt.date.today()} · 자본 5천만원 · 2010~ · 비용 차감 · 실현 손익 기준",
         "같은 날은 거래대금 작은 순 · 이미 가진 종목은 안 삼 · 판 돈은 다음 거래일부터", ""]
    res = []
    for name, per, pct, daily, *cap in CONFIGS:
        log(name + "…")
        r = simulate(g, days, CAPITAL, per, pct, daily, cap[0] if cap else None)
        res.append((name, r))
    L.append(f"  {'방식':<34} {'최종':>9} {'연 수익':>7} {'2021~':>7} {'최대 낙폭':>8} {'낙폭(원)':>9} "
             f"{'연속 손실':>14} {'노는 돈':>6}")
    for name, r in res:
        L.append(f"  {name:<34} {r['final'] / 1e8:>7.2f}억 {r['cagr']:>+6.1f}% {r['oos_cagr'] or 0:>+6.1f}% "
                 f"{r['mdd']:>7.1f}% {r['mdd_won'] / 1e4:>7,.0f}만 {r['worst_n']:>3}번 {r['worst_sum'] / 1e4:>7,.0f}만 "
                 f"{r['idle']:>5.0f}%")
    L += ["", "해마다 수익률 (그해 초 계좌 대비)"]
    for name, r in res:
        yy = [f"{y} {v:+.0f}%" for y, v in r["by_year"].items()]
        L.append(f"  ■ {name}")
        for k in range(0, len(yy), 9):
            L.append("    " + "  ".join(yy[k:k + 9]))
    L += ["", "읽는 법: 최대 낙폭 = 가장 좋았을 때에서 가장 많이 빠졌을 때(%·원) · 연속 손실 = 손절이 이어진 가장 나쁜 구간의 "
              "건수와 합계 · 노는 돈 = 거래일마다 계좌 중 현금 비율의 평균",
          "비율 방식은 계좌가 커질수록 1회 금액도 커져 수익과 손실 금액이 함께 커진다. 아주 커지면 거래대금 작은 종목은 "
          "원하는 만큼 못 살 수 있다(호가 영향은 여기서 빼고 계산).",
          f"소요 {round((time.time() - t0) / 60, 1)}분"]
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"size-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
