"""v2b 목표가 낮추기: 보유 날이 지날수록 목표를 낮추면 (예: 3일째 5%, 4일째부터 3%) 손실을 줄일 수 있을까.

  python -m sbt.data.cli v2b-target

v2b 신호·매수(다음 날 시가)·손절 -7%·보유 5거래일은 그대로, '그날의 목표가'만 바꾼다.
날 번호: 0 = 산 날, 1~5 = 그 뒤 거래일 (5일째 장 마감에 팔림 — 앱 자동 매매와 같음).
하루 안에서 목표·손절이 모두 닿으면 손절로 친다 (일봉만으로는 순서를 몰라 불리하게).
아침 시가가 이미 그날 목표 이상이면 시가에 판다 (목표를 낮춘 날 아침에 흔함). 비용은 백테스트와 같음.
1부. 지금 규칙(목표 10%)으로 산 종목들이 어떻게 끝났나: 목표·손절·기간 만료 비율, 5일 안에 +3%·+5% 에 한 번이라도 닿은 비율.
2부. 목표 일정별 비교: 거래 1건당 · 과거(~2020)/검증(2021~) · 5천만 계좌(10% × 하루 10, 1회 최대 2천만).
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
from .research2 import _signals, _sim
from .research7 import V2B, Leg
from .research10 import simulate as account

CUT = np.datetime64("2021-01-01")
D = 5
STOP = 7.0
# (이름, 날 0~5 의 목표 %) — None 이면 목표 없음(손절·만료만)
PLANS: list[tuple[str, list[float | None]]] = [
    ("지금: 끝까지 10%", [10, 10, 10, 10, 10, 10]),
    ("3일째 5% · 4일째부터 3%", [10, 10, 10, 5, 3, 3]),
    ("3일째 5% · 4일째 3% · 5일째 본전", [10, 10, 10, 5, 3, 0]),
    ("3일째부터 5%", [10, 10, 10, 5, 5, 5]),
    ("2일째부터 5%", [10, 10, 5, 5, 5, 5]),
    ("점점: 10·10·8·6·4·2", [10, 10, 8, 6, 4, 2]),
    ("처음부터 5%", [5, 5, 5, 5, 5, 5]),
    ("처음부터 7%", [7, 7, 7, 7, 7, 7]),
    ("처음부터 3%", [3, 3, 3, 3, 3, 3]),
    ("(비교) 목표 없이 5일", [None] * 6),
]


class Book:
    """v2b 체결 거래들의 0~5일 시가·고가·저가·종가."""

    def __init__(self, runner: Runner):
        p = runner.panel
        self.spec = _family_spec(V2B, ["코스피", "코스닥"])
        t = _sim(runner, self.spec, _signals(runner, self.spec))
        ok = t.fill_row >= 0
        self.t = t
        self.ok = ok
        fr = t.fill_row[ok]
        self.fill = fr
        self.fp = t.fill_price[ok]
        idx = fr[:, None] + np.arange(D + 1)[None, :]
        self.valid = idx <= p.gend[fr][:, None]
        ii = np.where(self.valid, idx, 0)
        self.O, self.H, self.L, self.C = (np.where(self.valid, p.cols[k][ii], np.nan) for k in ("open", "high", "low", "close"))
        self.complete = self.valid[:, D]
        self.idx = idx
        self.code = p.codes[t.sig[ok]].astype(str)
        self.dates = p.dates
        v = p.cols.get("value")
        self.value = v[t.sig[ok]] if v is not None else np.zeros(ok.sum())
        cs = self.spec.costs
        self.buy_cost = 1 + (cs.buy_fee_pct + cs.slippage_pct) / 100
        self.sell_keep = 1 - (cs.sell_fee_pct + cs.sell_tax_pct + cs.slippage_pct) / 100
        self.engine_ret = t.ret_c[ok]

    def run(self, plan: list[float | None]) -> dict:
        n = len(self.fp)
        fp = self.fp
        stop = fp * (1 - STOP / 100)
        price = np.full(n, np.nan)
        kind = np.full(n, "", dtype=object)
        day = np.full(n, -1)
        alive = np.ones(n, bool)
        for d in range(D + 1):
            v = self.valid[:, d] & alive
            o, h, lo, c = self.O[:, d], self.H[:, d], self.L[:, d], self.C[:, d]
            tp = plan[d]
            tgt = fp * (1 + tp / 100) if tp is not None else np.full(n, np.inf)
            with np.errstate(invalid="ignore"):
                if d > 0:  # 아침 시가에서 먼저 (갭)
                    gs = v & (o <= stop)
                    gt = v & ~gs & (o >= tgt)
                    for m, k, px in ((gs, "손절", o), (gt, "목표", o)):
                        price[m], kind[m], day[m] = px[m], k, d
                        alive &= ~m
                    v &= ~(gs | gt)
                hs = v & (lo <= stop)
                ht = v & ~hs & (h >= tgt)
            price[hs], kind[hs], day[hs] = stop[hs], "손절", d
            price[ht], kind[ht], day[ht] = tgt[ht], "목표", d
            alive &= ~(hs | ht)
            if d == D:
                m = v & ~(hs | ht)
                price[m], kind[m], day[m] = c[m], "만료", d
                alive &= ~m
        done = day >= 0
        ret = np.where(done, ((price * self.sell_keep) / (fp * self.buy_cost) - 1) * 100, 0.0)
        exit_row = np.where(done, self.fill + np.maximum(day, 0), -1)
        g = Leg.__new__(Leg)
        g.name, g.per, g.daily = "v2b", 0, 0
        g.code = self.code
        g.fill = self.dates[self.fill]
        g.done = done
        g.exit = np.where(done, self.dates[np.maximum(exit_row, 0)], np.datetime64("NaT"))
        g.ret = ret
        g.value = self.value
        return {"leg": g, "kind": kind, "day": day, "ret": ret, "done": done}


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
    base = bk.run(PLANS[0][1])
    d = base["done"]
    gap = float(np.nanmean(np.abs(base["ret"][d] - bk.engine_ret[d]))) if d.any() else 0.0
    past = bk.dates[bk.fill] < CUT
    L = [f"v2b 목표가 낮추기 · {dt.date.today()} · 주가 ~{pd.Timestamp(panel.dates.max()).date()} · 2010~ · 손절 -7% · 5거래일",
         "날 0 = 산 날, 1~5 = 그 뒤 거래일 · 같은 날 목표·손절 둘 다 닿으면 손절로(불리하게) · 비용 차감",
         f"(검산: 지금 규칙을 이 계산으로 다시 한 것과 백테스트 엔진의 1건 수익률 차이 평균 {gap:.3f}%p)", ""]

    # 1부
    k, r, dy = base["kind"][d], base["ret"][d], base["day"][d]
    n = len(r)
    L += [f"1부. 지금 규칙으로 산 {n:,}건은 어떻게 끝났나"]
    for lab in ("목표", "손절", "만료"):
        m = k == lab
        L.append(f"  {lab:<4} {m.mean() * 100:>5.1f}%  ({m.sum():>6,}건 · 1건 평균 {r[m].mean() if m.any() else 0:+.2f}%)")
    exp = k == "만료"
    if exp.any():
        L.append(f"  기간 만료 중 플러스로 끝난 것 {(r[exp] > 0).mean() * 100:.0f}% · 마이너스 {(r[exp] <= 0).mean() * 100:.0f}%")
    L.append(f"  10건 사면 대략: 목표 {(k == '목표').mean() * 10:.1f} · 손절 {(k == '손절').mean() * 10:.1f} · 기간 만료 {exp.mean() * 10:.1f}")
    L.append("  끝난 날: " + " · ".join(f"{x}일째 {(dy == x).mean() * 100:.0f}%" for x in range(D + 1)))
    # 손절 전에 +3·+5 에 닿았나 (지금 규칙이 끝나는 날까지)
    fp = bk.fp[d]
    Hd = bk.H[d]
    reach = {}
    for lv in (3, 5, 7):
        hit = np.zeros(n, bool)
        for x in range(D + 1):
            hit |= (x <= dy) & (Hd[:, x] >= fp * (1 + lv / 100))
        reach[lv] = hit
    L.append("  끝나기 전에 한 번이라도 닿은 비율: " + " · ".join(f"+{lv}% {reach[lv].mean() * 100:.0f}%" for lv in reach))
    miss = reach[5] & (k != "목표")
    L.append(f"  +5% 까지 갔다가 목표(10%)는 못 가고 끝난 것 {miss.mean() * 100:.0f}% → 그중 손절 {(k[miss] == '손절').mean() * 100:.0f}% · "
             f"만료 {(k[miss] == '만료').mean() * 100:.0f}% · 1건 평균 {r[miss].mean() if miss.any() else 0:+.2f}%")

    # 2부
    log("목표 일정별 비교…")
    days = np.unique(panel.dates)
    cap = 50_000_000
    L += ["", "2부. 목표 일정별 (날 0·1·2·3·4·5 의 목표 %)",
          f"  {'':<30} {'일정':<17} {'1건 평균':>8} {'수익 비율':>7} {'목표':>5} {'손절':>5} {'만료':>5} "
          f"{'과거':>7} {'검증':>7}  {'계좌 연':>7} {'2021~':>7} {'최대 낙폭':>8}"]
    res = []
    for name, plan in PLANS:
        log(name + "…")
        x = bk.run(plan)
        dd = x["done"]
        rr, kk = x["ret"][dd], x["kind"][dd]
        pp = past[dd]
        acc = account(x["leg"], days, cap, None, 0.10, 10, 20_000_000)
        sched = "·".join("-" if v is None else f"{v:g}" for v in plan)
        res.append((name, acc))
        L.append(f"  {name:<30} {sched:<17} {rr.mean():>+7.2f}% {(rr > 0).mean() * 100:>6.0f}% "
                 f"{(kk == '목표').mean() * 100:>4.0f}% {(kk == '손절').mean() * 100:>4.0f}% {(kk == '만료').mean() * 100:>4.0f}% "
                 f"{rr[pp].mean():>+6.2f}% {rr[~pp].mean():>+6.2f}%  {acc['cagr']:>+6.1f}% {acc['oos_cagr'] or 0:>+6.1f}% "
                 f"{acc['mdd']:>7.1f}%")
    L += ["", "  해마다 계좌 수익률 (그해 초 대비)"]
    for name, acc in res[:5]:
        yy = [f"{y} {v:+.0f}%" for y, v in acc["by_year"].items()]
        L.append(f"  ■ {name}")
        for i in range(0, len(yy), 9):
            L.append("    " + "  ".join(yy[i:i + 9]))
    L += ["", "읽는 법: '1건 평균'과 '계좌 연'이 지금보다 높고, 과거와 검증 둘 다 좋아지면 바꿀 만함.",
          "목표를 낮추면 수익 비율(이긴 거래 비율)은 거의 늘지만, 크게 오를 종목을 일찍 팔아 1건 평균은 줄 수 있음 — 둘을 같이 볼 것.",
          "계좌는 실현 손익 기준 · 5천만 · 그날 계좌의 10% × 하루 10종목, 1회 최대 2천만 (지금 설정).",
          f"소요 {round((time.time() - t0) / 60, 1)}분"]
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"v2b-target-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
