"""중간값 '번트' 시험: 목표를 짧게(+2.5%), 손절은 장대양봉 저가, 3% 이상 갭상승 양봉은 뺀다.

  python -m sbt.data.cli bunt-test

앱에 저장된 '중간값' 전략의 신호·지정가를 그대로 쓰고 청산만 바꾼다.
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
from .engine import event
from .engine.run import Runner
from .engine.spec import parse_text
from .engine.stats import summarize
from .research import _family_spec
from .research2 import Ctx, _fmt, _signals
from .research7 import V2B, Leg, portfolio

CUT = "2021-01-01"
NO_GAP = "시가 < 종가(1) * 1.03"  # 3% 이상 갭상승으로 시작한 양봉은 뺀다


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
    low = panel.cols["low"]

    def make(*, target=None, stop=None, hold=None, gap=True):
        ex = base.exit.model_copy(update={"target_pct": target, "stop_pct": stop,
                                          "max_days": hold or base.exit.max_days})
        sig = f"({base.signal}) 그리고 {NO_GAP}" if gap else base.signal
        return base.model_copy(update={"exit": ex, "signal": sig})

    rows = []

    def one(label, spec, low_stop=True):
        log(label + "…")
        sig = np.sort(_signals(runner, spec))
        t = event.simulate(panel, sig, spec, runner._limit(spec), None, runner._exit(spec),
                           stop_level=low if low_stop else None)
        s = ctx.stats(t, spec)
        done = (t.fill_row >= 0) & (t.outcome_c != event.OPEN)
        # 손절 거리: 매수가(지정가)에서 신호일 저가까지
        dist = (1 - low[t.sig] / t.fill_price) * 100
        dist = dist[done & np.isfinite(dist)]
        out = {k: int((t.outcome_c[done] == v).sum()) for k, v in
               (("tgt", event.TARGET), ("stop", event.STOP), ("time", event.TIMEOUT))}
        rows.append((label, spec, s, out, float(np.median(dist)) if len(dist) else None, low_stop))
        return s

    L = [f"중간값 번트 시험 · {dt.date.today()} · '{st['name']}' · 비용 차감(수수료·세금 0.23%) · 과거 ~2020 / 검증 2021~",
         f"신호 [{base.signal}] · 지정가 {base.entry.price} ({base.entry.valid_days}일) · 기본 보유 {base.exit.max_days}일",
         f"번트: 목표 +2.5% · 손절 = 장대양봉(신호일) 저가 · 갭상승 3% 이상 양봉 제외 [{NO_GAP}]", ""]
    one("원래 (목표 5 / 손절 2%, 갭 포함)", base, low_stop=False)
    one("번트 (목표 2.5 / 저가 손절 / 갭 제외)", make(target=2.5))
    one("번트, 갭 제외 안 함", make(target=2.5, gap=False))
    one("번트, 손절 없음 (기간 만료까지)", make(target=2.5), low_stop=False)
    for tg in (2.0, 3.0, 4.0, 5.0):
        one(f"목표 {tg} / 저가 손절 / 갭 제외", make(target=tg))
    for hd in (3, 10):
        one(f"번트, 보유 {hd}일", make(target=2.5, hold=hd))
    for label, spec, s, out, dist, _ in rows:
        n = max(1, sum(out.values()))
        L.append(ctx.line(label, s))
        L.append(f"  {'':<22} 목표 {out['tgt'] / n * 100:.0f}% · 손절 {out['stop'] / n * 100:.0f}% · 기간 만료 "
                 f"{out['time'] / n * 100:.0f}%" + (f" · 매수가→손절선 거리 중간값 {dist:.1f}%" if dist else ""))
    # 번트 연도별
    b = next(r for r in rows if r[0].startswith("번트 (목표 2.5"))
    sig = np.sort(_signals(runner, b[1]))
    t = event.simulate(panel, sig, b[1], runner._limit(b[1]), None, runner._exit(b[1]), stop_level=low)
    yrs = panel.dates[t.sig].astype("datetime64[Y]").astype(int) + 1970
    yy = []
    for y in np.unique(yrs):
        s_ = summarize(t, b[1], yrs == y)
        if s_["n_complete"]:
            yy.append(f"{y} {_fmt(s_['avg_ret'], 1)}({s_['n_complete']})")
    L += ["", "번트 해마다 (1건당 평균, 건수)"] + ["    " + "  ".join(yy[k:k + 6]) for k in range(0, len(yy), 6)]

    # 계좌로: 저가 손절이 들어간 거래는 Leg 가 엔진을 직접 부르지 않으므로 여기서 만든다
    L += ["", "v2b(500만×10) + 중간값 버전(300만×5), 5천만 계좌 · 돈 같이 쓰기"]
    v2b = Leg("v2b", runner, _family_spec(V2B, ["코스피", "코스닥"]), 5_000_000, 10)
    r = portfolio([v2b])
    L.append(f"  {'v2b만':<34} 연 {r['cagr']:+.1f}% · 낙폭 {r['mdd']:.1f}% · 최종 {r['final'] / 1e8:,.2f}억")
    for label, spec, s, _, _, low_stop in rows[:2] + sorted(rows[2:], key=lambda r: -((r[2]["is"] or -9) + (r[2]["oos"] or -9)))[:2]:
        g = _leg(runner, spec, low if low_stop else None)
        r = portfolio([v2b, g])
        L.append(f"  {'+ ' + label:<34} 연 {r['cagr']:+.1f}% · 낙폭 {r['mdd']:.1f}% · 최종 {r['final'] / 1e8:,.2f}억 · "
                 f"중간값 {r['taken'][1]:,}건 {r['pnl_leg'][1] / 1e4:+,.0f}만")
    L.append(f"소요 {round((time.time() - t0) / 60, 1)}분")
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"bunt-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path


def _leg(runner, spec, stop_level) -> Leg:
    """Leg 와 같은 모양을 손절 가격선까지 넣어 만든다."""
    p = runner.panel
    g = Leg.__new__(Leg)
    t = event.simulate(p, np.sort(_signals(runner, spec)), spec, runner._limit(spec), None, runner._exit(spec),
                       stop_level=stop_level)
    ok = t.fill_row >= 0
    g.name, g.per, g.daily = "중간값", 3_000_000, 5
    g.code = p.codes[t.sig[ok]].astype(str)
    g.nm = p.names[t.sig[ok]].astype(str)
    g.fill = p.dates[t.fill_row[ok]]
    g.done = t.outcome_c[ok] != event.OPEN
    er = t.exit_row_c[ok]
    g.exit = np.where(er >= 0, p.dates[np.maximum(er, 0)], np.datetime64("NaT"))
    g.ret = np.where(g.done, t.ret_c[ok], 0.0)
    v = p.cols.get("value")
    g.value = v[t.sig[ok]] if v is not None else np.zeros(ok.sum())
    return g
