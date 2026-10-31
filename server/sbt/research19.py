"""실적 가속 중형주 계좌 다듬기 + v2b 와 같은 계좌 vs 따로.

  python -m sbt.data.cli accel-plan     (먼저 dart-collect)

신호: 코스피 101~300위(공시 전달 말 시총 순위) 회사가 '2분기 연속 영업이익 +20% 이상이고 가속'을 정기보고서로 공시.
      (비교로 '증익 가속' = +30% 이상이고 가속.) 공시 다음 거래일 종가에 산다.
매일 굴린다: 새 종목 1개 = 그날 계좌 평가액 ÷ 최대 종목 수 (자리가 없거나 현금이 모자라면 건너뜀).
정리: 보유 기간이 차면 · (선택) 산 값보다 -15% · (선택) 50일선 아래로 마감. 살 때 0.015%, 팔 때 0.23%. 배당·현금 이자 제외.
2부: 1억 원을 v2b(지금 설정: 계좌 10% × 하루 10종목, 1회 최대 2천만)와 실적 가속에
     ① 따로 5천만씩 ② 한 계좌에서 같이 (v2b 가 먼저 돈을 씀) 굴려 비교.
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import breadth, config
from .data import dart
from .data.indices import attach as indices_attach
from .data.indices import load as index_load
from .data.panel import Panel
from .engine.run import Runner
from .research import _family_spec
from .research14 import PERIODS, kospi_frame
from .research15 import _leg
from .research18 import events
from .research7 import V2B

BUY, SELL = 0.00015, 0.0023


class Market:
    """매일 굴리기에 필요한 표 (코스피 보통주)."""

    def __init__(self, panel: Panel):
        df = kospi_frame(panel, ("KOSPI",))
        df["close"] = panel.cols["close"][df["row"].to_numpy()]
        self.R = df.pivot_table(index="date", columns="code", values="ret")
        C = df.pivot_table(index="date", columns="code", values="close").reindex(self.R.index)
        self.days = self.R.index
        self.dates = self.days.to_numpy(dtype="datetime64[ns]")
        self.Rv = np.nan_to_num(self.R.to_numpy())
        self.Cv = C.to_numpy()
        self.MAv = C.rolling(50, min_periods=50).mean().to_numpy()
        self.RK = df.pivot_table(index="date", columns="code", values="rank").reindex(
            index=self.R.index, columns=self.R.columns).to_numpy()
        self.col = {c: j for j, c in enumerate(self.R.columns)}


def accel_signals(mk: Market, ev: pd.DataFrame, key: str) -> dict[int, list[int]]:
    """{매수일 인덱스: [종목 열, ...]} — 101~300위만."""
    e = ev[ev[key].fillna(False) & ev["code"].isin(mk.col)].copy()
    t0 = np.searchsorted(mk.dates, pd.to_datetime(e["rcept"]).to_numpy(dtype="datetime64[ns]"), side="right")
    out: dict[int, list[int]] = {}
    for t, code in zip(t0, e["code"]):
        if t >= len(mk.dates):
            continue
        j = mk.col[code]
        rk = mk.RK[t, j]
        if np.isfinite(rk) and 100 < rk <= 300:
            out.setdefault(int(t), []).append(j)
    return out


def v2b_trades(runner: Runner, mk: Market) -> dict[int, list[tuple]]:
    """{매수일 인덱스: [(코드, 청산일 인덱스, 수익률%, 거래대금), ...]} (v2b, 체결·청산 끝난 것)."""
    spec = _family_spec(V2B, ["코스피", "코스닥"])
    g = _leg(runner, spec, None, "v2b")
    fill = np.searchsorted(mk.dates, g.fill.astype("datetime64[ns]"), side="left")
    ex = np.searchsorted(mk.dates, np.where(np.isnat(g.exit), np.datetime64("2100-01-01"), g.exit).astype("datetime64[ns]"),
                         side="left")
    out: dict[int, list[tuple]] = {}
    for k in np.lexsort((np.nan_to_num(g.value, nan=np.inf), fill)):
        if not g.done[k] or fill[k] >= len(mk.dates):
            continue
        out.setdefault(int(fill[k]), []).append((g.code[k], int(ex[k]), float(g.ret[k])))
    return out


def simulate(mk: Market, capital: float, acc: dict | None, v2b: dict | None, *, max_n: int = 20, hold: int = 120,
             stop: float | None = None, ma_exit: bool = False, start: int = 0) -> dict:
    """한 계좌에서 매일. v2b 가 있으면 v2b 가 먼저 돈을 쓴다."""
    n = len(mk.dates)
    cash = capital
    pos: dict[int, list] = {}  # 열 → [평가액, 매수 인덱스, 매수가]
    vopen: list[list] = []  # [청산 인덱스, 돌려받을 돈, 원금, 코드]
    vheld: set[str] = set()
    eq = np.zeros(n)
    inv = np.zeros(n)
    taken = skipped = a_taken = a_skipped = 0
    for t in range(start, n):
        # 실적 가속: 오늘 등락 반영
        for j in pos:
            pos[j][0] *= 1 + mk.Rv[t, j]
        # v2b 청산 (청산일 다음 날부터 돈을 다시 쓴다 — research10 과 같음)
        keep = []
        for e_, back, cost, code in vopen:
            if e_ < t:
                cash += back
                vheld.discard(code)
            else:
                keep.append([e_, back, cost, code])
        vopen = keep
        equity = cash + sum(p[0] for p in pos.values()) + sum(v[2] for v in vopen)
        # v2b 매수 (먼저)
        if v2b and t in v2b:
            nb = 0
            for code, ex, r in v2b[t]:
                if nb >= 10 or code in vheld:
                    continue
                amt = min(equity * 0.10, 20_000_000)
                if cash < amt:
                    skipped += 1
                    continue
                cash -= amt
                nb += 1
                taken += 1
                vheld.add(code)
                vopen.append([ex, amt * (1 + r / 100), amt, code])
        # 실적 가속 매수 (오늘 종가)
        if acc and t in acc:
            for j in acc[t]:
                if j in pos:
                    continue
                amt = equity / max_n
                if len(pos) >= max_n or cash < amt or not np.isfinite(mk.Cv[t, j]):
                    a_skipped += 1
                    continue
                cash -= amt
                pos[j] = [amt * (1 - BUY), t, mk.Cv[t, j]]
                a_taken += 1
        # 실적 가속 정리 (오늘 종가)
        for j in list(pos):
            v, t_in, px_in = pos[j]
            px = mk.Cv[t, j]
            out = t - t_in >= hold
            if np.isfinite(px):
                out |= bool(stop and px <= px_in * (1 - stop))
                out |= bool(ma_exit and t > t_in and np.isfinite(mk.MAv[t, j]) and px < mk.MAv[t, j])
            if out:
                cash += v * (1 - SELL)
                del pos[j]
        stock = sum(p[0] for p in pos.values()) + sum(v[2] for v in vopen)
        eq[t] = cash + stock
        inv[t] = stock / eq[t] if eq[t] > 0 else 0.0
    s = pd.Series(eq[start:], index=mk.days[start:])
    yrs = max((s.index[-1] - s.index[0]).days / 365.25, 0.5)
    return {"eq": s, "final": float(s.iloc[-1]), "cagr": ((s.iloc[-1] / capital) ** (1 / yrs) - 1) * 100,
            "mdd": float((s / s.cummax() - 1).min() * 100), "inv": float(inv[start:].mean() * 100),
            "v2b": taken, "v2b_skip": skipped, "acc": a_taken, "acc_skip": a_skipped}


def _cells(s: pd.Series) -> str:
    r = s.pct_change().fillna(0)
    out = []
    for _, a, b in PERIODS[2:-1]:
        seg = r[(r.index >= a) & (r.index <= b)]
        out.append(f"{((1 + seg).prod() - 1) * 100:+.0f}%" if len(seg) else "-")
    return "".join(f"{c:>12}" for c in out)


def run(dataset: str = "kr", log=print) -> Path:
    t0 = time.time()
    raw = dart.load_raw()
    if raw.empty:
        raise SystemExit("DART 재무 자료가 없습니다. 연구 탭 'DART 재무 자료 받기'를 먼저 돌려 주세요.")
    log("데이터 읽는 중…")
    panel = Panel.load(config.dataset_dir(dataset) / "prices.parquet")
    if not indices_attach(panel, dataset):
        raise SystemExit("지수 데이터가 없습니다.")
    breadth.attach(panel)
    runner = Runner(panel, {"name": dataset, "markets": ["KOSPI", "KOSDAQ"], "currency": "KRW"})
    mk = Market(panel)
    ev = events(raw)
    sig2 = accel_signals(mk, ev, "accel2")
    sig1 = accel_signals(mk, ev, "accel")
    start = min(min(sig2, default=0), min(sig1, default=0))
    head = f"  {'':<38} {'배수':>6} {'연':>7} {'최대 낙폭':>8} {'투자':>5} {'산 것':>6} {'건너뜀':>6}" + \
        "".join(f"{n[:9]:>12}" for n, _, _ in PERIODS[2:-1])

    def row(label: str, r: dict, cap: float) -> str:
        return (f"  {label:<38} {r['final'] / cap:>5.1f}배 {r['cagr']:>+6.1f}% {r['mdd']:>7.1f}% {r['inv']:>4.0f}% "
                f"{r['acc']:>6,} {r['acc_skip']:>6,}" + _cells(r["eq"]))

    L = [f"실적 가속 중형주 계좌 · {dt.date.today()} · 주가 ~{mk.days[-1].date()} · {mk.days[start].date()}~ 매일",
         "신호: 코스피 101~300위 · 2분기 연속 영업이익 +20% 이상 가속 (정기보고서 다음 거래일 종가) · 새 종목 = 계좌 ÷ 최대 종목 수", "",
         "1부. 혼자 굴리기 (1억, 표시 없으면 최대 20종목 · 6개월 보유 · 정리 규칙 없음)", head]
    cap = 100_000_000
    log("1부…")
    variants = [
        ("기본: 20종목 · 6개월", dict()),
        ("보유 3개월", dict(hold=60)),
        ("보유 1년", dict(hold=250)),
        ("최대 10종목", dict(max_n=10)),
        ("최대 30종목", dict(max_n=30)),
        ("+ 종목 -15% 정리", dict(stop=0.15)),
        ("+ 종목 50일선 정리", dict(ma_exit=True)),
        ("+ -15% 와 50일선", dict(stop=0.15, ma_exit=True)),
        ("1년 보유 + -15% 정리", dict(hold=250, stop=0.15)),
    ]
    for label, kw in variants:
        log(label + "…")
        L.append(row(label, simulate(mk, cap, sig2, None, start=start, **kw), cap))
    L.append(row("(비교) 신호 '증익 가속' · 기본", simulate(mk, cap, sig1, None, start=start), cap))
    k = index_load("KOSPI")
    if k is not None:
        ks = k[(k.index >= mk.days[start]) & (k.index <= mk.days[-1])]
        if len(ks) > 1:
            L.append(f"  (같은 기간 코스피 지수 {ks.iloc[-1] / ks.iloc[0]:.1f}배 · 투자 = 주식에 들어가 있던 비율 평균 · "
                     "건너뜀 = 자리나 돈이 없어 못 산 신호)")

    # 2부
    log("2부: v2b 와 같이…")
    vt = v2b_trades(runner, mk)
    half = cap / 2
    L += ["", "2부. 1억 원: v2b 와 실적 가속(기본 20종목 · 6개월)을 따로 vs 한 계좌",
          f"  {'':<38} {'최종':>8} {'연':>7} {'최대 낙폭':>8} {'투자':>5} {'v2b 산 것':>9} {'v2b 못 삼':>9}"]
    only = simulate(mk, cap, None, vt, start=start)
    a = simulate(mk, half, None, vt, start=start)
    b = simulate(mk, half, sig2, None, start=start)
    sep_eq = a["eq"] + b["eq"]
    yrs = max((sep_eq.index[-1] - sep_eq.index[0]).days / 365.25, 0.5)
    sep = {"final": float(sep_eq.iloc[-1]), "cagr": ((sep_eq.iloc[-1] / cap) ** (1 / yrs) - 1) * 100,
           "mdd": float((sep_eq / sep_eq.cummax() - 1).min() * 100), "inv": (a["inv"] + b["inv"]) / 2,
           "v2b": a["v2b"], "v2b_skip": a["v2b_skip"]}
    both = simulate(mk, cap, sig2, vt, start=start)
    both10 = simulate(mk, cap, sig2, vt, start=start, max_n=40)
    for label, r in (("v2b 혼자 1억", only), ("따로: v2b 5천 + 실적 가속 5천", sep), ("한 계좌 1억 (v2b 먼저)", both),
                     ("한 계좌 1억 · 실적 가속 1종목 = 계좌 ÷ 40", both10)):
        L.append(f"  {label:<38} {r['final'] / 1e8:>6.2f}억 {r['cagr']:>+6.1f}% {r['mdd']:>7.1f}% {r['inv']:>4.0f}% "
                 f"{r['v2b']:>9,} {r['v2b_skip']:>9,}")
    L.append("  (v2b 는 실현 손익 기준이라 보유 중 평가는 원금으로 친다 · 못 삼 = 현금이 모자라 건너뛴 v2b 신호)")

    # 지금 들고 있을 종목
    names = dict(zip(panel.codes.astype(str), panel.names))
    cols = list(mk.R.columns)
    recent = [(t, j) for t, js in sig2.items() for j in js if t >= len(mk.dates) - 120]
    L += ["", f"최근 6개월 신호 {len(recent)}건 (기본 규칙이면 지금 들고 있을 종목):"]
    for t, j in sorted(recent, reverse=True)[:25]:
        px0, px = mk.Cv[t, j], mk.Cv[-1, j]
        L.append(f"  {mk.days[t].date()} {names.get(cols[j], cols[j]):<14} 산 뒤 {(px / px0 - 1) * 100:+.0f}% · "
                 f"50일선 {'위' if px >= mk.MAv[-1, j] else '아래'}")
    L += ["", "읽는 법: 1부에서 연 수익을 지키면서 낙폭이 줄어드는 조합이 실적 가속 계좌의 규칙 후보.",
          "2부에서 한 계좌가 따로보다 연 수익이 낮거나 v2b 를 많이 못 사면 따로 운영이 맞음.",
          f"소요 {round((time.time() - t0) / 60, 1)}분"]
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"accel-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
