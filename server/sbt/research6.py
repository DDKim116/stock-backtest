"""하락장·상승장 전략 후보 비교 (매수만 가능한 일봉 전략).

  python -m sbt.data.cli bear-test    # 하락장 후보
  python -m sbt.data.cli bull-test    # 상승장 후보

구분 기준
- 날 단위 (조건식): 상승기 = 그날 지수가 60일 이평선 위, 하락기 = 아래.
- 해 단위 (요약): 약세장 해 = 코스피 연간 -10% 이하 또는 그해 고점 대비 -20% 넘게 하락,
                 강세장 해 = 코스피 연간 +15% 이상.
전략마다 과거/검증 구간 성과, 상승기·하락기 날 신호의 성과, 약세장·강세장 해 성과를 따로 보여 준다.
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
from .engine import dsl
from .engine.run import Runner
from .engine.stats import summarize
from .research import _family_spec
from .research2 import Ctx, _fmt, _signals, _sim

CUT = "2021-01-01"
BEAR: list[tuple[str, str, str]] = [
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


BULL: list[tuple[str, str, str]] = [
    ("기준: 신고가 추세 v1", "추천 전략에 있던 것: 상승장에서 거래량 실린 52주 신고가 돌파, 50일선 깨면 정리", """[신호]
종가 > 이전(최고(고가, 250), 1)
그리고 거래량 >= 이평(거래량, 20) * 1.5
그리고 지수 > 이평(지수, 60)
[매수]
다음날시가
[청산]
목표 = 없음
손절 = 10
보유기간 = 120
청산조건 = 종가 < 이평(종가, 50)"""),
    ("눌림목", "오르는 종목(20일선 > 60일선)이 며칠 쉬며 20일선까지 내려왔다가 지킨 날", """[신호]
지수 > 이평(지수, 60)
그리고 이평(종가, 20) > 이평(종가, 60)
그리고 수익률(5) <= -3
그리고 저가 <= 이평(종가, 20) * 1.01
그리고 종가 > 이평(종가, 20)
[매수]
다음날시가
[청산]
목표 = 8
손절 = 5
보유기간 = 10"""),
    ("박스권 돌파", "20일 동안 15% 안에서 움직이던 종목이 거래량 2배로 박스 위로 뚫은 날", """[신호]
지수 > 이평(지수, 60)
그리고 종가 > 이전(최고(고가, 20), 1)
그리고 이전(최고(고가, 20), 1) <= 이전(최저(저가, 20), 1) * 1.15
그리고 거래량 >= 이평(거래량, 20) * 2
[매수]
다음날시가
[청산]
목표 = 없음
손절 = 6
추적손절 = 8
보유기간 = 20"""),
    ("골든크로스 + 시장 폭", "20일선이 60일선을 뚫고, 시장도 오르는 종목이 더 많은 때(상승비율 20일 평균 50% 이상)", """[신호]
상향돌파(이평(종가, 20), 이평(종가, 60))
그리고 지수 > 이평(지수, 60)
그리고 이평(상승비율, 20) >= 50
[매수]
다음날시가
[청산]
목표 = 없음
손절 = 10
보유기간 = 60
청산조건 = 종가 < 이평(종가, 60)"""),
    ("강한 마감 장대양봉", "상승장(지수 20일선 위)에서 +10% 넘게 오르고 고가 근처(-3% 이내)에서 마감, 거래대금 100억 이상", """[신호]
지수 > 이평(지수, 20)
그리고 등락률 >= 10
그리고 종가 >= 고가 * 0.97
그리고 거래대금 >= 100억
[매수]
다음날시가
[청산]
목표 = 7
손절 = 4
보유기간 = 3"""),
]


def _years(kind: str) -> list[int]:
    """약세장 해(bear) 또는 강세장 해(bull)."""
    s = index_load("KOSPI")
    if s is None:
        return []
    out = []
    for y, g in s.groupby(s.index.year):
        prev = s[s.index.year < y]
        if not len(prev):
            continue  # 첫해는 기준 종가가 없음
        ret = g.iloc[-1] / prev.iloc[-1] - 1
        dd = (g / g.cummax() - 1).min()
        if (kind == "bear" and (ret <= -0.10 or dd <= -0.20)) or (kind == "bull" and ret >= 0.15):
            out.append(int(y))
    return out


def run(dataset: str = "kr", kind: str = "bear", log=print) -> Path:
    t0 = time.time()
    root = config.dataset_dir(dataset)
    log("데이터 읽는 중…")
    panel = Panel.load(root / "prices.parquet")
    if not indices_attach(panel, dataset):
        raise SystemExit("지수 데이터가 없습니다. 먼저 index-update 를 실행하세요.")
    breadth.attach(panel)
    runner = Runner(panel, {"name": dataset, "markets": ["KOSPI", "KOSDAQ"], "currency": "KRW"})
    ctx = Ctx(runner, np.datetime64(CUT, "D"))
    bears, bulls = _years("bear"), _years("bull")
    up_day = dsl.evaluate("지수 > 이평(지수, 60)", panel)  # 날 단위 상승기
    title = "하락장" if kind == "bear" else "상승장"
    L = [f"{title} 전략 비교 · {dt.date.today()} · 국내 2010~ · 과거 ~{CUT} / 검증 {CUT}~ · 비용 차감, 거래 1건당 평균",
         "날 단위: 상승기 = 신호일 지수가 60일선 위, 하락기 = 아래",
         f"약세장 해(코스피 연간 -10% 이하 또는 고점 대비 -20% 넘게 하락): {', '.join(map(str, bears)) or '없음'}",
         f"강세장 해(코스피 연간 +15% 이상): {', '.join(map(str, bulls)) or '없음'}"]
    for name, idea, text in (BEAR if kind == "bear" else BULL):
        log(f"{name}…")
        spec = _family_spec(text, ["코스피", "코스닥"])
        t = _sim(runner, spec, _signals(runner, spec))
        st = ctx.stats(t, spec)
        yrs = panel.dates[t.sig].astype("datetime64[Y]").astype(int) + 1970
        row = []
        for y in np.unique(yrs):
            s_ = summarize(t, spec, yrs == y)
            if s_["n_complete"]:
                mark = "*" if y in bears else ("+" if y in bulls else "")
                row.append(f"{y}{mark} {_fmt(s_['avg_ret'], 1)}({s_['n_complete']})")
        L.append("")
        L.append(f"■ {name} — {idea}")
        L.append(ctx.line("전체", st))
        up = up_day[t.sig]
        for label, m in (("상승기 날 신호", up), ("하락기 날 신호", ~up),
                         ("강세장 해", np.isin(yrs, bulls)), ("약세장 해", np.isin(yrs, bears))):
            x = summarize(t, spec, m) if m.any() else None
            if x and x["n_complete"]:
                L.append(f"  {label:<14} {_fmt(x['avg_ret'], 2)} ({x['n_complete']:,}건) · 수익 비율 "
                         f"{_fmt(x.get('profit_rate'), 1, sign=False)}")
        for k in range(0, len(row), 6):
            L.append("    " + "  ".join(row[k:k + 6]))
    L.append("")
    L.append("* = 약세장 해, + = 강세장 해")
    L.append(f"소요 {round((time.time() - t0) / 60, 1)}분")
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{kind}-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
