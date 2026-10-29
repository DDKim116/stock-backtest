"""하락장에서 쓸 만한 전략 후보 비교 (매수만 가능한 일봉 전략).

  python -m sbt.data.cli bear-test

전략마다 과거/검증 구간 성과와, 지수가 크게 빠진 해(약세장 해)의 성과를 따로 보여 준다.
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np

from . import breadth, config
from .data.indices import attach as indices_attach
from .data.indices import load as index_load
from .data.panel import Panel
from .engine.run import Runner
from .engine.stats import summarize
from .research import _family_spec
from .research2 import Ctx, _fmt, _signals, _sim

CUT = "2021-01-01"
STRATEGIES: list[tuple[str, str, str]] = [
    ("기준: 급락 반등 v2b", "지금 쓰는 전략", """[신호]
수익률(5) <= -15
그리고 rsi(14) <= 25
그리고 지수 <= 이전(지수, 5) * 0.97
[매수]
다음날시가
[청산]
목표 = 10
손절 = 7
보유기간 = 5"""),
    ("투매 망치형", "시장 대부분이 빠진 날, 거래량 터지며 아래꼬리 길게 회복한 종목 (투매 끝 신호)", """[신호]
상승비율 <= 25
그리고 수익률(5) <= -10
그리고 거래량 >= 이평(거래량, 20) * 2
그리고 종가 >= 저가 + (고가 - 저가) * 0.6
[매수]
다음날시가
[청산]
목표 = 8
손절 = 5
보유기간 = 5"""),
    ("갭하락 회복", "약한 장에서 5% 넘게 갭하락 출발했다가 시가보다 3% 넘게 올려 마감 (패닉 매도 소화)", """[신호]
시가 <= 종가(1) * 0.95
그리고 종가 >= 시가 * 1.03
그리고 동일가중 < 이평(동일가중, 20)
[매수]
다음날시가
[청산]
목표 = 6
손절 = 4
보유기간 = 3"""),
    ("볼린저 하단 복귀", "하락장(지수 60일선 아래)에서 볼린저 하단을 깼다가 다시 안으로 들어온 날", """[신호]
이전(종가, 1) < 이전(이평(종가, 20) - 표준편차(종가, 20) * 2, 1)
그리고 종가 > 이평(종가, 20) - 표준편차(종가, 20) * 2
그리고 지수 < 이평(지수, 60)
[매수]
다음날시가
[청산]
목표 = 7
손절 = 5
보유기간 = 7"""),
    ("하락장 주도주", "지수는 60일선 아래인데 혼자 120일 고점 근처를 지키는 종목 (상대 강도). 20일선 깨면 정리", """[신호]
지수 < 이평(지수, 60)
그리고 종가 >= 최고(고가, 120) * 0.97
그리고 종가 > 이평(종가, 20)
그리고 거래대금 >= 30억
[매수]
다음날시가
[청산]
목표 = 없음
손절 = 8
보유기간 = 40
청산조건 = 종가 < 이평(종가, 20)"""),
]


def _bear_years() -> list[int]:
    """코스피가 그해 -10% 넘게 빠졌거나 고점 대비 -20% 넘게 빠진 해."""
    s = index_load("KOSPI")
    if s is None:
        return []
    out = []
    for y, g in s.groupby(s.index.year):
        prev = s[s.index.year < y]
        base = prev.iloc[-1] if len(prev) else g.iloc[0]
        ret = g.iloc[-1] / base - 1
        dd = (g / g.cummax() - 1).min()
        if ret <= -0.10 or dd <= -0.20:
            out.append(int(y))
    return out


def run(dataset: str = "kr", log=print) -> Path:
    t0 = time.time()
    root = config.dataset_dir(dataset)
    log("데이터 읽는 중…")
    panel = Panel.load(root / "prices.parquet")
    if not indices_attach(panel, dataset):
        raise SystemExit("지수 데이터가 없습니다. 먼저 index-update 를 실행하세요.")
    breadth.attach(panel)
    runner = Runner(panel, {"name": dataset, "markets": ["KOSPI", "KOSDAQ"], "currency": "KRW"})
    ctx = Ctx(runner, np.datetime64(CUT, "D"))
    bears = _bear_years()
    L = [f"하락장 전략 비교 · {dt.date.today()} · 국내 2010~ · 과거 ~{CUT} / 검증 {CUT}~ · 비용 차감, 거래 1건당 평균",
         f"약세장 해(코스피 연간 -10% 이하 또는 고점 대비 -20% 이하): {', '.join(map(str, bears)) or '없음'}"]
    for name, idea, text in STRATEGIES:
        log(f"{name}…")
        spec = _family_spec(text, ["코스피", "코스닥"])
        t = _sim(runner, spec, _signals(runner, spec))
        st = ctx.stats(t, spec)
        yrs = panel.dates[t.sig].astype("datetime64[Y]").astype(int) + 1970
        bear = summarize(t, spec, np.isin(yrs, bears)) if bears else None
        row = []
        for y in np.unique(yrs):
            s = summarize(t, spec, yrs == y)
            if s["n_complete"]:
                row.append(f"{y}{'*' if y in bears else ''} {_fmt(s['avg_ret'], 1)}({s['n_complete']})")
        L.append("")
        L.append(f"■ {name} — {idea}")
        L.append(ctx.line("전체", st))
        if bear and bear["n_complete"]:
            L.append(f"  약세장 해만                {_fmt(bear['avg_ret'], 2)} ({bear['n_complete']:,}건) · "
                     f"수익 비율 {_fmt(bear.get('profit_rate'), 1, sign=False)}")
        for k in range(0, len(row), 6):
            L.append("    " + "  ".join(row[k:k + 6]))
    L.append("")
    L.append("* = 약세장 해")
    L.append(f"소요 {round((time.time() - t0) / 60, 1)}분")
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"bear-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
