"""추천 전략 확정: 2차 연구 결과에 '지수' 조건을 붙인 전략을 검증하고, 앱의 '전략' 탭에 저장한다.

  python -m sbt.data.cli recommend            # 검증만
  python -m sbt.data.cli recommend --save     # 검증 + 전략 탭에 저장 (같은 이름이면 덮어씀)
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np

from . import config
from .data.indices import attach as indices_attach
from .data.panel import Panel
from .engine.run import Runner
from .engine.spec import to_text
from .engine.stats import summarize
from .research import LIQ, _family_spec, _params
from .research2 import Ctx, _fmt, _signals, _sim

CUT = "2021-01-01"

RECOMMENDED: list[tuple[str, str, str]] = [
    ("급락 반등 v2 · 시장 폭락",
     "지수도 5일간 5% 넘게 빠진 날, 5일간 15% 넘게 빠지고 RSI 25 이하인 종목을 다음 날 시가에 사서 3~5일 안에 정리. 시장 폭락 뒤 과매도 반등.",
     """[신호]
수익률(5) <= -15
그리고 rsi(14) <= 25
그리고 지수 <= 이전(지수, 5) * 0.95
[매수]
다음날시가
[청산]
목표 = 10
손절 = 7
보유기간 = 5"""),
    ("급락 반등 v2b · 시장 하락 -3%",
     "v2보다 시장 조건을 완화 (지수 5일 -3% 이상 하락). 신호가 더 자주 나온다.",
     """[신호]
수익률(5) <= -15
그리고 rsi(14) <= 25
그리고 지수 <= 이전(지수, 5) * 0.97
[매수]
다음날시가
[청산]
목표 = 10
손절 = 7
보유기간 = 5"""),
    ("신고가 추세 v1 · 상승장",
     "지수가 60일선 위(상승장)일 때만, 거래량을 동반한 52주 신고가 돌파 종목을 사서 50일선을 깨면 판다. 수익 비율은 낮고 큰 수익 몇 번으로 버는 추세 추종.",
     """[신호]
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
    ("신고가 추세 v1b · 상승장 추적손절",
     "v1과 같은 진입, 청산은 고점 대비 12% 하락(추적손절)으로.",
     """[신호]
종가 > 이전(최고(고가, 250), 1)
그리고 거래량 >= 이평(거래량, 20) * 1.5
그리고 지수 > 이평(지수, 60)
[매수]
다음날시가
[청산]
목표 = 없음
손절 = 10
추적손절 = 12
보유기간 = 120"""),
]


def _without_index(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if "지수" not in ln)


def run(dataset: str = "kr", save: bool = False, log=print) -> Path:
    t0 = time.time()
    root = config.dataset_dir(dataset)
    log("데이터 읽는 중…")
    panel = Panel.load(root / "prices.parquet")
    if not indices_attach(panel, dataset):
        raise SystemExit("지수 데이터가 없습니다. 먼저 index-update 를 실행하세요.")
    runner = Runner(panel, {"name": dataset, "markets": ["KOSPI", "KOSDAQ"], "currency": "KRW"})
    ctx = Ctx(runner, np.datetime64(CUT, "D"))
    L = [f"추천 전략 검증 · {dt.date.today()} · 국내 2010~ · 과거 ~{CUT} / 검증 {CUT}~ · 비용 차감, 거래 1건당 평균"]
    saved = []
    store = None
    if save:
        from .api.store import Store

        store = Store(config.DATA_DIR / "app.sqlite3")
    for name, idea, text in RECOMMENDED:
        log(f"{name}…")
        spec = _family_spec(text, ["코스피", "코스닥"])
        t = _sim(runner, spec, _signals(runner, spec))
        st = ctx.stats(t, spec)
        base_spec = _family_spec(_without_index(text), ["코스피", "코스닥"])
        bt = _sim(runner, base_spec, _signals(runner, base_spec))
        bs = ctx.stats(bt, base_spec)
        yrs = panel.dates[t.sig].astype("datetime64[Y]").astype(int) + 1970
        row, pos, tot = [], 0, 0
        for y in np.unique(yrs):
            s = summarize(t, spec, yrs == y)
            if s["n_complete"]:
                row.append(f"{y} {_fmt(s['avg_ret'], 1)}({s['n_complete']})")
                if s["n_complete"] >= 10 and s["avg_ret"] is not None:
                    tot += 1
                    pos += s["avg_ret"] > 0
        days = len(np.unique(panel.dates[t.sig]))
        L.append("")
        L.append(f"■ {name}")
        L.append(f"  {idea}")
        L.append(f"  신호: {spec.signal.replace(LIQ, '').strip()}")
        L.append(f"  매매: {_params(spec)}")
        L.append(ctx.line("지수 조건 붙임", st))
        L.append(ctx.line("(지수 조건 없을 때)", bs))
        L.append(f"  신호 난 날 {days:,}일 · 수익 난 해 {pos}/{tot} · 데이터 끊김 {int(ctx.delisted(t).sum())}건")
        for k in range(0, len(row), 6):
            L.append("    " + "  ".join(row[k:k + 6]))
        if store is not None:
            same = next((x for x in store.list() if x["name"] == name), None)
            store.save(name, to_text(spec), note=f"추천 전략 ({dt.date.today()}) · {idea}",
                       sid=same["id"] if same else None,
                       last_summary=None)
            saved.append(name)
    L.append("")
    if saved:
        L.append(f"앱의 '전략' 탭에 {len(saved)}개를 저장했습니다: " + ", ".join(saved))
    L.append(f"소요 {round((time.time() - t0) / 60, 1)}분")
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"recommend-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
