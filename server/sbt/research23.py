"""미국판 v2b: 국장 v2b(5일 급락 + RSI 과매도 + 지수도 빠질 때 다음 날 시가)를 미국 전 종목에.

  python -m sbt.data.cli us-v2b

규칙은 국장 v2b 와 같은 모양, 거래대금은 2,000만 달러 이상 · 주가 5달러 이상 (미국은 동전주가 많아서).
비용: 사고팔 때 0.07% 씩 + 슬리피지 0.1% (해외 주식 수수료 수준), 세금 0 (양도세는 연 250만원 공제 뒤 22% — 따로 생각).
1부. 기준(국장과 같은 숫자)과 몇 가지 바꾼 규칙: 거래 1건당 평균 · 과거(~2020) / 검증(2021~) · 해마다.
2부. 계좌 굴리기 (research10 과 같은 규칙: 계좌 10% × 하루 10종목, 판 돈은 다음 날부터).
3부. 국장 v2b 와 같이 하면 도움이 될까: 달마다 손익의 상관, 둘 다 쉬는 달.
⚠ 미국 자료는 '지금 상장된 종목'만 있다 (망해서 사라진 회사가 빠짐 = 생존 편향). 급락 매수 전략은 이 편향에 가장 약하다:
  실제로는 급락 뒤 상장폐지된 회사도 샀을 것이므로 결과가 실제보다 좋게 나온다.
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import config
from .data.indices import attach as indices_attach
from .data.panel import Panel
from .engine.run import Runner
from .engine.spec import parse_text
from .research import _family_spec
from .research7 import V2B, Leg
from .research10 import simulate as account
from .research15 import _leg

CUT = np.datetime64("2021-01-01")
US_MARKETS = ["NASDAQ", "NYSE", "AMEX"]
US_TEXT = """[신호]
수익률(5) <= {drop}
그리고 rsi(14) <= {rsi}
그리고 지수 <= 이전(지수, 5) * {idx}
그리고 거래대금 >= {liq}
그리고 종가 >= {px}
[매수]
다음날시가
[청산]
목표 = {tgt}
손절 = {stop}
보유기간 = 5
[대상]
시장 = {markets}
기간 = 2010-01-01 ~ 오늘
제외 = 스팩
[비용]
매수수수료 = 0.07
매도수수료 = 0.07
세금 = 0
슬리피지 = 0.1"""
BASE = dict(drop=-15, rsi=25, idx=0.97, liq="2000만", px=5, tgt=10, stop=7)
VARIANTS: list[tuple[str, dict]] = [
    ("기준 (국장 v2b 와 같은 숫자)", {}),
    ("지수 조건 빼기", dict(idx=100)),
    ("지수 -2% (덜 빠져도)", dict(idx=0.98)),
    ("5일 -20% (더 깊게)", dict(drop=-20)),
    ("RSI 20 이하", dict(rsi=20)),
    ("거래대금 500만$ 이상", dict(liq="500만")),
    ("거래대금 1억$ 이상 (큰 회사)", dict(liq="1억")),
    ("주가 10$ 이상", dict(px=10)),
    ("목표 15 · 손절 10", dict(tgt=15, stop=10)),
]
LABEL = {"NASDAQ": "나스닥", "NYSE": "뉴욕", "AMEX": "아멕스", "KOSPI": "코스피", "KOSDAQ": "코스닥", "DEMO": "데모"}


def spec_text(markets: list[str], **kw) -> str:
    p = {**BASE, **kw}
    return US_TEXT.format(markets=", ".join(LABEL.get(m, m) for m in markets), **p)


def _stats(g: Leg) -> str:
    def one(m) -> str:
        r = g.ret[g.done & m]
        return f"{len(r):>6,}건 {r.mean():+5.2f}% {(r > 0).mean() * 100:>3.0f}%" if len(r) else f"{'-':>18}"
    past = g.fill < CUT
    return f"{one(np.ones(len(g.fill), bool))}  {one(past)}  {one(~past)}"


def monthly(g: Leg, per: float = 1.0) -> pd.Series:
    """달마다 '1건에 같은 돈' 실현 손익 합 (청산 달 기준) — 상관을 보려는 용도."""
    m = g.done & ~np.isnat(g.exit)
    if not m.any():
        return pd.Series(dtype=float)
    s = pd.Series(g.ret[m] * per / 100, index=pd.DatetimeIndex(g.exit[m]))
    return s.groupby(s.index.to_period("M")).sum()


def run(dataset: str = "us", kr_dataset: str = "kr", markets: list[str] | None = None, log=print) -> Path:
    t0 = time.time()
    markets = markets or US_MARKETS
    log("미국 데이터 읽는 중…")
    panel = Panel.load(config.dataset_dir(dataset) / "prices.parquet")
    if not indices_attach(panel, dataset):
        raise SystemExit("미국 지수 데이터가 없습니다 (index-update 먼저).")
    runner = Runner(panel, {"name": dataset, "markets": markets, "currency": "USD"})
    end = str(pd.Timestamp(panel.dates.max()).date())
    L = [f"미국판 v2b · {dt.date.today()} · 주가 ~{end} · 2010~ · 나스닥·뉴욕·아멕스 보통주",
         "다음 날 시가에 사서 목표/손절/5일 · 비용 왕복 0.14% + 슬리피지 0.1% · 세금 제외",
         "⚠ 생존 편향: 지금 상장된 종목만 있음 → 급락 뒤 상장폐지된 회사가 빠져 결과가 실제보다 좋게 나옴. 숫자를 깎아서 읽기.",
         "", "1부. 규칙별 거래 1건당 (건수 · 평균 · 수익 비율)",
         f"  {'':<30} {'전체':>18}  {'과거 ~2020':>18}  {'검증 2021~':>18}"]
    legs: dict[str, Leg] = {}
    for label, kw in VARIANTS:
        log(label + "…")
        g = _leg(runner, parse_text(spec_text(markets, **kw)), None, label)
        legs[label] = g
        L.append(f"  {label:<30} {_stats(g)}")
    base = legs[VARIANTS[0][0]]

    L += ["", "  해마다 (기준 규칙: 건수 · 1건당 평균)"]
    yrs = pd.DatetimeIndex(base.fill).year
    cells = []
    for y in sorted(set(yrs)):
        r = base.ret[base.done & (yrs == y)]
        if len(r):
            cells.append(f"{y} {len(r):>4}건 {r.mean():+.1f}%")
    for k in range(0, len(cells), 5):
        L.append("    " + "   ".join(cells[k:k + 5]))

    # 2부: 계좌
    log("계좌 굴리기…")
    days = np.unique(panel.dates)
    L += ["", "2부. 계좌 굴리기 (5천만원 어치 달러, 실현 손익 기준, 같은 날은 거래대금 작은 순)",
          f"  {'':<30} {'최종':>8} {'연':>7} {'2021~':>7} {'최대 낙폭':>8} {'노는 돈':>6} {'산 것':>6}"]
    cap = 50_000_000
    for label in (VARIANTS[0][0], VARIANTS[1][0], VARIANTS[6][0]):
        r = account(legs[label], days, cap, None, 0.10, 10, 20_000_000)
        L.append(f"  {label:<30} {r['final'] / 1e8:>6.2f}억 {r['cagr']:>+6.1f}% {r['oos_cagr'] or 0:>+6.1f}% "
                 f"{r['mdd']:>7.1f}% {r['idle']:>5.0f}% {r['taken']:>6,}")
    yy = [f"{y} {v:+.0f}%" for y, v in account(base, days, cap, None, 0.10, 10, 20_000_000)["by_year"].items()]
    L.append("  기준 해마다: " + "  ".join(yy))

    # 3부: 국장 v2b 와의 관계
    log("국장 v2b 와 비교…")
    try:
        kp = Panel.load(config.dataset_dir(kr_dataset) / "prices.parquet")
        if not indices_attach(kp, kr_dataset):
            raise FileNotFoundError("국장 지수 없음")
        kr = _leg(Runner(kp, {"name": kr_dataset, "markets": ["KOSPI", "KOSDAQ"], "currency": "KRW"}),
                  _family_spec(V2B, ["코스피", "코스닥"]), None, "국장 v2b")
        a, b = monthly(kr), monthly(base)
        idx = a.index.union(b.index)
        a, b = a.reindex(idx, fill_value=0.0), b.reindex(idx, fill_value=0.0)
        both_idle = ((a == 0) & (b == 0)).mean() * 100
        kr_idle = (a == 0).mean() * 100
        corr = float(np.corrcoef(a, b)[0, 1]) if len(idx) > 2 and a.std() > 0 and b.std() > 0 else float("nan")
        bad = (a < 0) & (b < 0)
        L += ["", "3부. 국장 v2b 와 같이 하면 (달마다, 1건에 같은 돈일 때 실현 손익)",
              f"  거래가 있던 달: 국장 {(a != 0).sum()} · 미국 {(b != 0).sum()} · 전체 {len(idx)}달",
              f"  국장이 쉰 달 {kr_idle:.0f}% → 둘 다 쉰 달 {both_idle:.0f}% (미국이 빈 달을 얼마나 채우나)",
              f"  달마다 손익 상관 {corr:+.2f} (0 에 가까울수록 서로 다른 때 벌고 잃음 · 1 이면 같이 움직임)",
              f"  둘 다 손실인 달 {int(bad.sum())}번"]
    except FileNotFoundError as e:
        L += ["", f"3부. 국장 자료가 없어 건너뜀 ({e})"]

    L += ["", "읽는 법: 검증(2021~) 1건당 평균이 +0.5% 이상이고 과거와 비슷하면 미국에서도 통하는 규칙.",
          "생존 편향 때문에 실제는 여기보다 낮다고 보고(급락주 일부는 상장폐지), 큰 회사만(거래대금 1억$↑) 결과를 더 믿을 만함.",
          "상관이 낮고 국장이 쉰 달을 미국이 채우면 '따로 계좌'로 같이 굴릴 이유가 있음. 환전·양도세(연 250만원 넘으면 22%)도 생각.",
          f"소요 {round((time.time() - t0) / 60, 1)}분"]
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"us-v2b-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
