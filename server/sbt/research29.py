"""v2b 체결 현실성: 사고팔 때 가격이 밀리면(슬리피지) · 주문이 커지면 · 최소 거래대금을 올리면 성적이 어떻게 바뀌나.

  python -m sbt.data.cli fill-test

백테스트는 '주문한 가격에 전부 바로 체결'(슬리피지 0)로 계산했다. 실제로는
- 아침 시가 매수 · 손절 · 기간만료 매도는 시장가라 가격이 조금 밀린다 (목표가 매도는 지정가라 안 밀림).
- 거래가 적은 종목일수록, 주문이 클수록 더 밀린다.
1부. 밀리는 폭을 똑같이 0~1%로 가정했을 때 (시장가 한 번마다).
2부. 주문 크기에 따라: 밀림 = 0.1% + 5% × √(주문 금액 ÷ 그날 거래대금) (거래량이 적을수록·주문이 클수록 커지는 흔한 모형).
     1회 100만~3,000만 원 — '이 전략에 얼마까지 넣을 수 있나'.
3부. 최소 거래대금을 10억(지금)에서 올리면: 신호 수 · 1건 평균 · 계좌. 고르는 순서(작은 순 vs 큰 순)도.
4부. 신호 날 거래대금 구간별: 어디서 수익이 나오나.
계좌: 5천만 · 그날 계좌의 10% × 하루 10종목(1회 최대 2천만) · 판 것 기준. 2부만 1회 고정 금액 × 10종목(자본 = 10배).
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
from .research7 import Leg
from .research10 import simulate as account
from .research26 import Book

CUT = np.datetime64("2021-01-01")
BASE_SLIP = 0.001  # 호가 한두 칸
IMPACT = 0.05  # 하루 변동 5% 가정


def run(dataset: str = "kr", log=print) -> Path:
    t0 = time.time()
    log("데이터 읽는 중…")
    panel = Panel.load(config.dataset_dir(dataset) / "prices.parquet")
    if not indices_attach(panel, dataset):
        raise SystemExit("지수 데이터가 없습니다.")
    breadth.attach(panel)
    runner = Runner(panel, {"name": dataset, "markets": ["KOSPI", "KOSDAQ"], "currency": "KRW"})
    log("v2b 신호·체결…")
    bk = Book(runner)
    x = bk.run([10] * 6)
    done, kind, ret0 = x["done"], x["kind"], x["ret"]
    base = x["leg"]
    value = np.where(np.isfinite(bk.value) & (bk.value > 0), bk.value, np.nan)
    past = bk.dates[bk.fill] < CUT
    market_sell = np.isin(kind, ["손절", "만료"])  # 목표가는 지정가라 안 밀림
    days = np.unique(panel.dates)
    n = int(done.sum())

    def slipped(buy: np.ndarray | float, sell: np.ndarray | float) -> np.ndarray:
        s = np.where(market_sell, sell, 0.0)
        return np.where(done, ((1 + ret0 / 100) * (1 - s) / (1 + buy) - 1) * 100, 0.0)

    def leg(ret: np.ndarray, mask: np.ndarray | None = None, big_first: bool = False) -> Leg:
        m = np.ones(len(ret), bool) if mask is None else mask
        g = Leg.__new__(Leg)
        g.name, g.per, g.daily = "v2b", 0, 0
        g.code, g.fill, g.done, g.exit = base.code[m], base.fill[m], base.done[m], base.exit[m]
        g.ret = ret[m]
        v = np.nan_to_num(value[m], nan=np.inf)
        g.value = -v if big_first else v
        return g

    def acct(g: Leg, per: float | None = None) -> dict:
        if per is None:
            return account(g, days, 50_000_000, None, 0.10, 10, 20_000_000)
        return account(g, days, per * 10, per, None, 10)

    def stat(ret: np.ndarray, m: np.ndarray | None = None) -> str:
        mm = done if m is None else done & m
        r = ret[mm]
        if not len(r):
            return f"{'-':>8}{'-':>7}{'-':>8}{'-':>8}"
        pa, va = ret[mm & past], ret[mm & ~past]
        return (f"{r.mean():>+7.2f}%{(r > 0).mean() * 100:>6.0f}%{(pa.mean() if len(pa) else float('nan')):>+7.2f}%"
                f"{(va.mean() if len(va) else float('nan')):>+7.2f}%")

    def arow(a: dict) -> str:
        return f"{a['cagr']:>+7.1f}%{a['oos_cagr'] or 0:>+7.1f}%{a['mdd']:>8.1f}%"

    head = f"{'1건 평균':>8}{'이긴':>7}{'과거':>8}{'검증':>8}{'계좌 연':>8}{'2021~':>8}{'최대 낙폭':>9}"
    L = [f"v2b 체결 현실성 · {dt.date.today()} · 주가 ~{pd.Timestamp(panel.dates.max()).date()} · 거래 {n:,}건 · 2010~",
         f"끝난 모양: 목표 {(kind[done] == '목표').mean() * 100:.0f}% · 손절 {(kind[done] == '손절').mean() * 100:.0f}% · "
         f"만료 {(kind[done] == '만료').mean() * 100:.0f}% (시장가로 파는 건 손절·만료)", ""]

    # 1부
    log("1부…")
    L += ["1부. 시장가 한 번마다 똑같이 밀린다고 하면 (살 때 + 손절·만료로 팔 때)", f"  {'밀리는 폭':<12}{head}"]
    for s in (0, 0.001, 0.002, 0.003, 0.005, 0.01):
        r = slipped(s, s)
        L.append(f"  {s * 100:>5.1f}%      {stat(r)}{arow(acct(leg(r)))}")
    r = slipped(0.0, 0.003)
    L.append(f"  팔 때만 0.3% {stat(r)}{arow(acct(leg(r)))}")

    # 2부
    log("2부…")
    L += ["", "2부. 주문이 커지면 (밀림 = 0.1% + 5% × √(주문 ÷ 그날 거래대금) · 1회 고정 금액 × 하루 10종목 · 자본 = 10배)",
          f"  {'1회 금액':<10}{'평균 밀림':>8}{head}"]
    for amt in (1e6, 3e6, 5e6, 1e7, 2e7, 3e7):
        sl = BASE_SLIP + IMPACT * np.sqrt(amt / value)
        sl = np.nan_to_num(sl, nan=0.05)
        r = slipped(sl, sl)
        L.append(f"  {amt / 1e4:>6,.0f}만  {np.nanmean(sl[done]) * 100:>6.2f}%  {stat(r)}{arow(acct(leg(r), per=amt))}")
    L.append("  (자본이 커지면 1회 금액도 커진다: 계좌 10% 규칙이면 5천만 → 500만, 1억 → 1,000만, 2억 → 2,000만)")

    # 3부
    log("3부…")
    L += ["", "3부. 최소 거래대금을 올리면 (밀림: 위 모형, 1회 500만 기준 / 괄호 없는 줄은 밀림 0)",
          f"  {'최소 거래대금':<14}{'신호':>7}{'한 해':>6}  {head}"]
    yrs = max((days[-1] - days[0]).astype(int) / 365.25, 1)
    sl5 = np.nan_to_num(BASE_SLIP + IMPACT * np.sqrt(5e6 / value), nan=0.05)
    r5 = slipped(sl5, sl5)
    for lo in (1e9, 3e9, 5e9, 1e10, 3e10):
        m = np.nan_to_num(value, nan=0) >= lo
        k = int((done & m).sum())
        if k < 30:
            continue
        lab = f"{lo / 1e8:,.0f}억 이상"
        L.append(f"  {lab:<14}{k:>7,}{k / yrs:>6.0f}  {stat(ret0, m)}{arow(acct(leg(ret0, m)))}")
        L.append(f"  {'  (밀림 반영)':<14}{'':>7}{'':>6}  {stat(r5, m)}{arow(acct(leg(r5, m)))}")
    L += ["", "  신호가 많은 날 고르는 순서 (최소 10억 · 밀림 반영)", f"  {'':<14}{'계좌 연':>8}{'2021~':>8}{'최대 낙폭':>9}"]
    for lab, big in (("거래대금 작은 순 (지금)", False), ("거래대금 큰 순", True)):
        L.append(f"  {lab:<14}{arow(acct(leg(r5, None, big)))}")

    # 4부
    L += ["", "4부. 신호 날 거래대금 구간별 (밀림 0 / 500만 주문 밀림 반영)",
          f"  {'구간':<14}{'건수':>7}{'목표':>6}{'손절':>6}{'만료':>6}{'1건 평균':>9}{'밀림 반영':>9}{'500만이 차지':>11}"]
    for lo, hi, lab in ((1e9, 3e9, "10~30억"), (3e9, 1e10, "30~100억"), (1e10, 3e10, "100~300억"), (3e10, 1e11, "300~1,000억"),
                        (1e11, 1e15, "1,000억~")):
        m = done & (np.nan_to_num(value, nan=0) >= lo) & (np.nan_to_num(value, nan=0) < hi)
        if m.sum() < 20:
            continue
        kk = kind[m]
        L.append(f"  {lab:<14}{int(m.sum()):>7,}{(kk == '목표').mean() * 100:>5.0f}%{(kk == '손절').mean() * 100:>5.0f}%"
                 f"{(kk == '만료').mean() * 100:>5.0f}%{ret0[m].mean():>+8.2f}%{r5[m].mean():>+8.2f}%"
                 f"{np.nanmedian(5e6 / value[m]) * 100:>9.2f}%")
    L += ["  ('500만이 차지' = 500만 원 주문이 그날 그 종목 거래대금에서 차지하는 비율, 중간값)", "",
          "읽는 법: 1부에서 밀림 0.3~0.5%에도 계좌 연 수익이 두 자릿수면 튼튼한 편.",
          "2부에서 1회 금액이 커질 때 수익이 꺾이는 곳이 이 전략에 넣을 수 있는 돈의 한계 (예: 2,000만에서 꺾이면 계좌 2억 근처).",
          "3부에서 최소 거래대금을 올려도 '밀림 반영' 수익이 비슷하거나 낫다면 올리는 게 체결·모의투자 지연 모두에 유리.",
          "밀림 모형은 가정이다 (실제는 종목·시각마다 다름). 실투 초기에 실제 체결가와 비교해 맞춰 봐야 한다.",
          f"소요 {round((time.time() - t0) / 60, 1)}분"]
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"fill-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
