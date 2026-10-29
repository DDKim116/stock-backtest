"""급락 반등 v2b 의 '시장 하락' 조건 비교: 지수(시총 가중) 대신 상승비율·동일가중(시장 폭)으로.

  python -m sbt.data.cli breadth-test
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np

from . import breadth, config
from .data.indices import attach as indices_attach
from .data.panel import Panel
from .engine.run import Runner
from .engine.stats import summarize
from .research import _family_spec
from .research2 import Ctx, _fmt, _signals, _sim

CUT = "2021-01-01"
BASE = """[신호]
수익률(5) <= -15
그리고 rsi(14) <= 25
그리고 {cond}
[매수]
다음날시가
[청산]
목표 = 10
손절 = 7
보유기간 = 5"""
VARIANTS: list[tuple[str, str | None]] = [
    ("지금 v2b: 지수 5일 -3%", "지수 <= 이전(지수, 5) * 0.97"),
    ("시장 조건 없음", None),
    ("상승비율 <= 20 (그날)", "상승비율 <= 20"),
    ("상승비율 <= 30 (그날)", "상승비율 <= 30"),
    ("5일 평균 상승비율 <= 35", "이평(상승비율, 5) <= 35"),
    ("5일 평균 상승비율 <= 40", "이평(상승비율, 5) <= 40"),
    ("동일가중 5일 -3%", "동일가중 <= 이전(동일가중, 5) * 0.97"),
    ("동일가중 5일 -5%", "동일가중 <= 이전(동일가중, 5) * 0.95"),
    ("지수 -3% 그리고 5일 평균 상승비율 <= 40", "지수 <= 이전(지수, 5) * 0.97 그리고 이평(상승비율, 5) <= 40"),
]


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
    L = [f"v2b 시장 조건 비교 · {dt.date.today()} · 국내 2010~ · 과거 ~{CUT} / 검증 {CUT}~ · 비용 차감, 거래 1건당 평균",
         "공통: 수익률(5) <= -15 그리고 rsi(14) <= 25 · 다음날 시가 · 목표 10 / 손절 7 / 5일"]
    for name, cond in VARIANTS:
        log(f"{name}…")
        text = BASE.format(cond=cond) if cond else "\n".join(ln for ln in BASE.splitlines() if "{cond}" not in ln)
        spec = _family_spec(text, ["코스피", "코스닥"])
        t = _sim(runner, spec, _signals(runner, spec))
        st = ctx.stats(t, spec)
        yrs = panel.dates[t.sig].astype("datetime64[Y]").astype(int) + 1970
        pos = tot = 0
        for y in np.unique(yrs):
            s = summarize(t, spec, yrs == y)
            if s["n_complete"] >= 10 and s["avg_ret"] is not None:
                tot += 1
                pos += s["avg_ret"] > 0
        days = len(np.unique(panel.dates[t.sig]))
        L.append("")
        L.append(f"■ {name}" + (f"   [{cond}]" if cond else ""))
        L.append(ctx.line("결과", st))
        L.append(f"  신호 난 날 {days:,}일 · 수익 난 해 {pos}/{tot}")
    L.append("")
    L.append(f"소요 {round((time.time() - t0) / 60, 1)}분")
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"breadth-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
