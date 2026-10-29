"""중간값 전략 살리기: 앱에 저장된 '중간값' 전략을 한 가지씩 바꿔 가며 비교한다.

  python -m sbt.data.cli mid-test

1부. 목표·손절 조합 (나머지는 그대로)
2부. 시장 조건을 더했을 때 / 매수 가격(지정가)을 더 낮췄을 때 / 주문 유효기간
3부. 두 구간 모두 플러스인 상위 3개를 v2b 와 5천만 계좌로 같이 굴려 보기 (300만×하루 5)
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np

from . import breadth, config
from .api.store import Store
from .data.indices import attach as indices_attach
from .data.panel import Panel
from .engine.run import Runner
from .engine.spec import parse_text
from .research import _family_spec
from .research2 import Ctx, _fmt, _signals, _sim
from .research7 import V2B, Leg, portfolio

CUT = "2021-01-01"


def _variant(spec, *, target=None, stop=None, hold=None, market=None, price=None, valid=None):
    ex = spec.exit.model_copy(update={k: v for k, v in (("target_pct", target), ("stop_pct", stop),
                                                         ("max_days", hold)) if v is not None})
    en = spec.entry.model_copy(update={k: v for k, v in (("price", price), ("valid_days", valid)) if v is not None})
    sig = f"({spec.signal}) 그리고 {market}" if market else spec.signal
    return spec.model_copy(update={"exit": ex, "entry": en, "signal": sig})


def run(dataset: str = "kr", log=print) -> Path:
    t0 = time.time()
    st = next((x for x in Store(config.DATA_DIR / "app.sqlite3").list() if "중간값" in x["name"]), None)
    if not st:
        raise SystemExit("앱 '전략' 탭에 이름에 '중간값'이 들어간 전략이 없습니다.")
    base = parse_text(st["text"])
    panel = Panel.load(config.dataset_dir(dataset) / "prices.parquet")
    if not indices_attach(panel, dataset):
        raise SystemExit("지수 데이터가 없습니다.")
    breadth.attach(panel)
    runner = Runner(panel, {"name": dataset, "markets": ["KOSPI", "KOSDAQ"], "currency": "KRW"})
    ctx = Ctx(runner, np.datetime64(CUT, "D"))
    x0 = base.exit
    L = [f"중간값 전략 살리기 · {dt.date.today()} · '{st['name']}' · 비용 차감, 거래 1건당 평균 · 과거 ~2020 / 검증 2021~",
         f"원래: 신호 [{base.signal}] · 지정가 {base.entry.price} ({base.entry.valid_days}일) · "
         f"목표 {x0.target_pct} / 손절 {x0.stop_pct} / {x0.max_days}일"]
    results = []

    def one(label: str, spec) -> dict:
        log(label + "…")
        t = _sim(runner, spec, _signals(runner, spec))
        s = ctx.stats(t, spec)
        results.append((label, spec, s))
        return s

    L += ["", "1부. 목표 · 손절 (나머지 그대로)"]
    L.append(f"  {'목표/손절':<10} " + "  ".join(f"손절 {sp:>4}" for sp in (2, 3, 5, 7, 10)))
    for tg in (5, 8, 10, 15):
        cells = []
        for sp in (2, 3, 5, 7, 10):
            s = one(f"목표 {tg} / 손절 {sp}", _variant(base, target=tg, stop=sp))
            cells.append(f"{_fmt(s['is'], 1)}/{_fmt(s['oos'], 1)}")
        L.append(f"  목표 {tg:<6} " + "  ".join(f"{c:>13}" for c in cells))
    L.append("  (칸 = 과거/검증 1건당 평균)")

    L += ["", "2부. 한 가지씩 바꾸기 (목표·손절은 원래대로)"]
    tries = [
        ("원래 그대로", {}),
        ("+ 지수 60일선 위 (상승장만)", {"market": "지수 > 이평(지수, 60)"}),
        ("+ 지수 60일선 아래 (하락장만)", {"market": "지수 < 이평(지수, 60)"}),
        ("+ 상승비율 20일 평균 50↑", {"market": "이평(상승비율, 20) >= 50"}),
        ("+ 동일가중 20일선 위", {"market": "동일가중 > 이평(동일가중, 20)"}),
        ("지정가 = 시가 + (종가-시가)×0.3 (더 낮게)", {"price": "시가 + (종가 - 시가) * 0.3"}),
        ("지정가 = 시가 (장대양봉 시작점)", {"price": "시가"}),
        ("주문 유효기간 3일", {"valid": 3}),
        ("주문 유효기간 10일", {"valid": 10}),
        ("보유기간 10일", {"hold": 10}),
    ]
    for label, kw in tries:
        L.append(ctx.line(label, one(label, _variant(base, **kw))))

    good = [r for r in results if (r[2]["is"] or -9) > 0 and (r[2]["oos"] or -9) > 0 and r[2]["n"] >= 200]
    good.sort(key=lambda r: -((r[2]["is"] or 0) + (r[2]["oos"] or 0)))
    L += ["", f"두 구간 모두 플러스 (200건 이상): {len(good)}개"]
    for label, _, s in good[:8]:
        L.append(ctx.line(label, s))

    L += ["", "3부. v2b(500만×10) + 중간값 버전(300만×5), 5천만 계좌 · 돈 같이 쓰기"]
    v2b = Leg("v2b", runner, _family_spec(V2B, ["코스피", "코스닥"]), 5_000_000, 10)
    r = portfolio([v2b])
    L.append(f"  {'v2b만':<36} 연 {r['cagr']:+.1f}% · 낙폭 {r['mdd']:.1f}% · 최종 {r['final'] / 1e8:,.2f}억")
    for label, spec, _ in [("원래 중간값", base, None)] + good[:3]:
        g = Leg("중간값", runner, spec, 3_000_000, 5)
        r = portfolio([v2b, g])
        L.append(f"  {'+ ' + label:<36} 연 {r['cagr']:+.1f}% · 낙폭 {r['mdd']:.1f}% · 최종 {r['final'] / 1e8:,.2f}억 · "
                 f"중간값 {r['taken'][1]:,}건 {r['pnl_leg'][1] / 1e4:+,.0f}만")
    L.append(f"소요 {round((time.time() - t0) / 60, 1)}분")
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"mid-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
