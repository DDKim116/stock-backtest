"""하루 신호가 많을 때 어떤 순서로 고를지: 급락 반등 v2b 를 '하루 최대 n종목, 1회 매수금 고정'으로 돌려 비교한다.

  python -m sbt.data.cli rank                       # 하루 5종목 · 1회 500만원 · 자본 5천만/1억
  python -m sbt.data.cli rank --per 5000000 --daily 5 --capital 50000000,100000000

고르는 기준은 모두 신호일(전날 저녁)에 이미 알 수 있는 값만 쓴다.
A. 자금 제한 없이 하루 n종목만: 거래 1건당 평균 (과거 ~2020 / 검증 2021~)
B. 자금 제한 포함 (매도한 돈은 다음 거래일부터 다시 씀): 최종 자산, 연 수익률, 최대 낙폭(실현 손익 기준)
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
from .engine import dsl, event
from .engine.run import Runner
from .research import _family_spec
from .research2 import _fmt, _signals, _sim
from .research3 import RECOMMENDED

CUT = np.datetime64("2021-01-01", "D")
STRATEGY = "급락 반등 v2b · 시장 하락 -3%"
RANDOM_SEEDS = 20

# (이름, 신호일 값 → 작을수록 먼저)
RULES = [
    ("RSI 낮은 순", lambda f: f["rsi"]),
    ("5일 하락 큰 순", lambda f: f["ret5"]),
    ("당일 하락 큰 순", lambda f: f["change"]),
    ("당일 하락 작은 순", lambda f: -f["change"]),
    ("거래대금 큰 순", lambda f: -f["value"]),
    ("거래대금 작은 순", lambda f: f["value"]),
    ("시가총액 큰 순", lambda f: -f["mktcap"]),
    ("시가총액 작은 순", lambda f: f["mktcap"]),
]


def _features(runner: Runner, sig: np.ndarray) -> dict[str, np.ndarray]:
    p = runner.panel
    ev = lambda e: np.asarray(dsl.evaluate(e, p, runner.ev), dtype=float)[sig]  # noqa: E731
    c = p.cols
    nan = np.full(len(sig), np.nan)
    return {
        "rsi": ev("rsi(14)"),
        "ret5": ev("수익률(5)"),
        "change": c["change"][sig] if "change" in c else ev("등락률"),
        "value": c["value"][sig] if "value" in c else nan,
        "mktcap": c["mktcap"][sig] if "mktcap" in c else nan,
    }


def _pick(day: np.ndarray, score: np.ndarray, n: int) -> np.ndarray:
    """날짜별로 score 작은 순 n개 (값이 없으면 뒤로). 원래 배열에서의 위치를 돌려준다."""
    s = np.where(np.isfinite(score), score, np.inf)
    df = pd.DataFrame({"k": np.arange(len(day)), "d": day, "s": s}).sort_values(["d", "s"], kind="stable")
    return df.groupby("d").head(n)["k"].to_numpy()


def _avg(ret: np.ndarray, m: np.ndarray) -> float | None:
    r = ret[m]
    return float(r.mean()) if len(r) else None


class Book:
    """자금 제한 포함 포트폴리오 (실현 손익 기준)."""

    def __init__(self, capital: float, per: float, daily: int):
        self.capital, self.per, self.daily = capital, per, daily

    def run(self, order: np.ndarray, fill_d, exit_d, ret, done) -> dict:
        """order: 신호일·우선순위 순으로 정렬된 거래 위치들."""
        cash = self.capital
        open_: list[tuple[np.datetime64, float]] = []  # (청산일, 돌아올 돈)
        taken, skipped, stuck = 0, 0, 0
        realized = []  # (날짜, 실현 손익)
        per_day: dict = {}
        for k in order:
            d = fill_d[k]
            # 전날까지 청산된 돈을 돌려받는다
            keep = []
            for e, back in open_:
                if e < d:
                    cash += back
                    realized.append((e, back - self.per))
                else:
                    keep.append((e, back))
            open_ = keep
            if per_day.get(d, 0) >= self.daily:
                continue
            if cash < self.per:
                skipped += 1
                continue
            cash -= self.per
            per_day[d] = per_day.get(d, 0) + 1
            taken += 1
            if done[k]:
                open_.append((exit_d[k], self.per * (1 + ret[k] / 100)))
            else:
                stuck += 1  # 끝나지 않은 거래(최근 또는 데이터 끊김)는 돈이 묶인 채로 둔다
        for e, back in open_:
            cash += back
            realized.append((e, back - self.per))
        locked = stuck * self.per
        final = cash + locked  # 끝나지 않은 거래는 원금으로 친다
        if not realized:
            return {"taken": taken, "skipped": skipped, "final": final}
        rs = pd.Series([v for _, v in realized], index=pd.DatetimeIndex([d for d, _ in realized])).sort_index()
        eq = self.capital + rs.cumsum()
        mdd = float(((eq / eq.cummax()) - 1).min() * 100)
        years = max((rs.index[-1] - rs.index[0]).days / 365.25, 0.5)
        cagr = ((final / self.capital) ** (1 / years) - 1) * 100
        by_year = rs.groupby(rs.index.year).sum() / self.capital * 100
        return {"taken": taken, "skipped": skipped, "final": final, "cagr": cagr, "mdd": mdd,
                "years_pos": int((by_year > 0).sum()), "years": len(by_year), "by_year": by_year,
                "oos_pnl": float(rs[rs.index >= pd.Timestamp(CUT)].sum())}


def run(dataset: str = "kr", per: float = 5_000_000, daily: int = 5,
        capitals: tuple[float, ...] = (50_000_000, 100_000_000), log=print) -> Path:
    t0 = time.time()
    root = config.dataset_dir(dataset)
    log("데이터 읽는 중…")
    panel = Panel.load(root / "prices.parquet")
    if not indices_attach(panel, dataset):
        raise SystemExit("지수 데이터가 없습니다. 먼저 index-update 를 실행하세요.")
    runner = Runner(panel, {"name": dataset, "markets": ["KOSPI", "KOSDAQ"], "currency": "KRW"})
    text = next(tx for name, _, tx in RECOMMENDED if name == STRATEGY)
    spec = _family_spec(text, ["코스피", "코스닥"])
    log("신호 계산 중…")
    sig = np.sort(_signals(runner, spec))
    t = _sim(runner, spec, sig)
    ok = t.fill_row >= 0
    sig_k, t_sig = np.flatnonzero(ok), t.sig[ok]
    feats = {k: v[ok] for k, v in _features(runner, t.sig).items()}
    done = (t.outcome_c[ok] != event.OPEN)
    ret = np.where(done, t.ret_c[ok], 0.0)
    sday = panel.dates[t_sig]
    fill_d = panel.dates[t.fill_row[ok]]
    exit_d = np.where(t.exit_row_c[ok] >= 0, panel.dates[np.maximum(t.exit_row_c[ok], 0)], np.datetime64("NaT"))
    is_m, oos_m = sday < CUT, sday >= CUT
    per_day = pd.Series(sday).value_counts()
    L = [f"하루 고르기 순서 비교 · {dt.date.today()} · {STRATEGY}",
         f"신호(체결) {len(sig_k):,}건 · 신호 난 날 {len(per_day):,}일 · 하루 {daily}개 넘은 날 {(per_day > daily).sum():,}일"
         f" (가장 많은 날 {str(per_day.index[0])[:10]} {per_day.iloc[0]}개)",
         f"1회 {per / 1e4:,.0f}만원 · 하루 최대 {daily}종목 · 비용 차감 · 과거 ~2020 / 검증 2021~",
         "", "A. 자금 제한 없이 하루 최대 n종목만 샀을 때 거래 1건당 평균"]
    L.append(f"  {'(전부 샀을 때)':<14} 과거 {_fmt(_avg(ret, done & is_m))} · 검증 {_fmt(_avg(ret, done & oos_m))}"
             f" · {int(done.sum()):,}건")

    rows = []
    rng = np.random.default_rng(0)
    rand = []
    for _ in range(RANDOM_SEEDS):
        pk = _pick(sday, rng.random(len(sday)), daily)
        m = np.zeros(len(sday), bool)
        m[pk] = True
        rand.append((_avg(ret, m & done & is_m), _avg(ret, m & done & oos_m)))
    r_is = float(np.mean([a for a, _ in rand]))
    r_oos = float(np.mean([b for _, b in rand]))
    L.append(f"  {'무작위 (20번 평균)':<14} 과거 {_fmt(r_is)} · 검증 {_fmt(r_oos)}")
    for name, fn in RULES:
        score = fn(feats)
        if not np.isfinite(score).any():
            continue
        pk = _pick(sday, score, daily)
        m = np.zeros(len(sday), bool)
        m[pk] = True
        a_is, a_oos = _avg(ret, m & done & is_m), _avg(ret, m & done & oos_m)
        rows.append((name, score, a_is, a_oos, int((m & done).sum())))
    rows.sort(key=lambda r: -((r[2] or -99) + (r[3] or -99)))
    for name, _, a_is, a_oos, n in rows:
        L.append(f"  {name:<14} 과거 {_fmt(a_is)} · 검증 {_fmt(a_oos)} · {n:,}건"
                 f"{'  ← 무작위보다 두 구간 모두 나음' if (a_is or -99) > r_is and (a_oos or -99) > r_oos else ''}")

    L += ["", "B. 자금 제한 포함 (매도 대금은 다음 거래일부터 다시 사용, 실현 손익 기준)"]
    candidates = [("무작위", rng.random(len(sday)))] + [(n, s) for n, s, *_ in rows[:4]]
    best = None
    for cap in capitals:
        L.append(f"  ■ 자본 {cap / 1e4:,.0f}만원 (동시 보유 최대 {int(cap // per)}종목)")
        for name, score in candidates:
            s = np.where(np.isfinite(score), score, np.inf)
            order = np.lexsort((s, fill_d))  # 체결일 순, 같은 날은 우선순위 순
            r = Book(cap, per, daily).run(order, fill_d, exit_d, ret, done)
            if "cagr" not in r:
                continue
            L.append(f"    {name:<14} 최종 {r['final'] / 1e8:,.2f}억 · 연 {r['cagr']:+.1f}% · 최대 낙폭 {r['mdd']:.1f}%"
                     f" · 수익 난 해 {r['years_pos']}/{r['years']} · 산 {r['taken']:,}건 · 돈 부족으로 못 산 {r['skipped']:,}건")
            if name != "무작위" and (best is None or r["cagr"] > best[1]):
                best = (name, r["cagr"], cap)
    L.append("")
    if rows:
        top = rows[0]
        beats = (top[2] or -99) > r_is and (top[3] or -99) > r_oos
        L.append(f"추천 순서: {top[0]}" + ("" if beats else " (단, 무작위와 차이가 작아 순서의 효과는 크지 않음)"))
    L.append(f"소요 {round((time.time() - t0) / 60, 1)}분")
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"rank-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
