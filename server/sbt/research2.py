"""2차 전략 연구: 1차에서 살아남은 '단기 급락 반등'을 꼼꼼히 검증하고, '52주 신고가'에 시장 필터를 붙여 본다.

  python -m sbt.data.cli research2

A. 급락 반등 숫자 정밀 탐색 (과거/검증 구간, 이웃 숫자 안정성)
B. 가장 나은 조합 정밀 점검
   - 상장폐지·장기 거래정지로 데이터가 끊긴 거래를 손실로 넣어도 남는지
   - 코스피 / 코스닥 따로
   - 시장 전체가 급락한 날 vs 종목만 급락한 날
   - 하루 신호가 많을 때 몇 개만 고르면 (거래대금 큰 순 / 더 많이 빠진 순)
   - 지수가 60일선 위/아래일 때
   - 연도별 성과, 신호가 몰린 날
C. 52주 신고가 추세에 '지수가 60일선 위일 때만' 필터를 붙이면
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import config
from .data import indices
from .data.panel import Panel
from .engine import dsl, event
from .engine.run import Runner
from .engine.spec import Spec
from .engine.stats import summarize
from .research import LIQ, _family_spec, _params

DELIST_GAP_DAYS = 20  # 종목 데이터가 전체 마지막 날보다 이만큼(달력일) 일찍 끝났으면 '끊김'
DELIST_ASSUME = -50.0  # 끊긴 거래를 보수적으로 볼 때 가정 손실(%)

REBOUND_GRID = """[신호]
수익률(5) <= [-10, -15, -20]
그리고 rsi(14) <= [25, 30, 35]
[매수]
다음날시가
[청산]
목표 = [5, 10]
손절 = 7
보유기간 = [3, 5, 7]"""

REBOUND_BASE = """[신호]
수익률(5) <= -15
그리고 rsi(14) <= 30
[매수]
다음날시가
[청산]
목표 = 10
손절 = 7
보유기간 = 5"""

HIGH_TREND = [
    ("52주 신고가 + 50일선", """[신호]
종가 > 이전(최고(고가, 250), 1)
그리고 거래량 >= 이평(거래량, 20) * 1.5
[매수]
다음날시가
[청산]
목표 = 없음
손절 = 10
보유기간 = 120
청산조건 = 종가 < 이평(종가, 50)"""),
    ("52주 신고가 추적손절 12%", """[신호]
종가 > 이전(최고(고가, 250), 1)
그리고 거래량 >= 이평(거래량, 20) * 1.5
[매수]
다음날시가
[청산]
목표 = 없음
손절 = 10
추적손절 = 12
보유기간 = 120"""),
]


# ---------------------------------------------------------------- 도구
def _subset(t: event.Trades, m: np.ndarray) -> event.Trades:
    return event.Trades(**{f.name: getattr(t, f.name)[m] for f in dataclasses.fields(t)})


def _signals(runner: Runner, spec: Spec) -> np.ndarray:
    p = runner.panel
    cond = dsl.evaluate(spec.signal, p, runner.ev)
    sig = np.flatnonzero(cond & runner._universe(spec))
    return event.dedupe(sig, p, spec.dedupe_days)


def _sim(runner: Runner, spec: Spec, sig: np.ndarray) -> event.Trades:
    return event.simulate(runner.panel, np.sort(sig), spec, runner._limit(spec), runner._fx(spec), runner._exit(spec))


def _fmt(x, nd=2, sign=True) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "-"
    return f"{x:+.{nd}f}%" if sign else f"{x:.{nd}f}%"


class Ctx:
    def __init__(self, runner: Runner, cut: np.datetime64):
        self.r = runner
        self.p = runner.panel
        self.cut = cut
        self.last_day = self.p.dates.max()
        # 지수 (코스피 종목엔 코스피, 코스닥 종목엔 코스닥)
        self.idx = {}
        for mk, key in (("KOSPI", "KOSPI"), ("KOSDAQ", "KOSDAQ")):
            s = indices.load(key)
            if s is not None:
                self.idx[mk] = s

    def stats(self, t: event.Trades, spec: Spec, mask=None) -> dict:
        tt = t if mask is None else _subset(t, mask)
        d = self.p.dates[tt.sig]
        a = summarize(tt, spec)
        i = summarize(tt, spec, d < self.cut)
        o = summarize(tt, spec, d >= self.cut)
        return {"n": a["n_complete"], "avg": a["avg_ret"], "is": i["avg_ret"], "is_n": i["n_complete"],
                "oos": o["avg_ret"], "oos_n": o["n_complete"], "win": a["profit_rate"], "succ": a["success_rate"]}

    def line(self, label: str, s: dict) -> str:
        return (f"  {label:<22} 과거 {_fmt(s['is'])} ({s['is_n']:,}건) · 검증 {_fmt(s['oos'])} ({s['oos_n']:,}건)"
                f" · 수익 비율 {_fmt(s['win'], 1, False)}")

    # 상장폐지 등으로 데이터가 끊겨 '진행중'으로 남은 거래
    def delisted(self, t: event.Trades) -> np.ndarray:
        p = self.p
        open_ = (t.outcome_c == event.OPEN) & (t.fill_row >= 0)
        end_day = p.dates[p.gend[t.sig]]
        gap = (self.last_day - end_day).astype(int)
        return open_ & (gap >= DELIST_GAP_DAYS)

    def market_series(self, sig: np.ndarray, kind: str) -> np.ndarray:
        """신호일의 지수 값: kind = ret5 (5일 수익률 %) | above60 (60일선 위면 1)"""
        p = self.p
        out = np.full(len(sig), np.nan)
        for mk, s in self.idx.items():
            m = p.markets[sig] == mk
            if not m.any():
                continue
            if kind == "ret5":
                v = (s / s.shift(5) - 1) * 100
            else:
                v = (s > s.rolling(60).mean()).astype(float)
            dates = pd.DatetimeIndex(p.dates[sig[m]])
            full = v.reindex(v.index.union(dates.unique())).ffill()
            out[m] = full.reindex(dates).to_numpy()
        return out


def _cap(ctx: Ctx, sig: np.ndarray, n: int, key: str) -> np.ndarray:
    """하루에 n개만: key = value(거래대금 큰 순) | drop(5일 하락 큰 순)"""
    p = ctx.p
    if key == "value":
        score = -p.cols["value"][sig] if "value" in p.cols else np.zeros(len(sig))
    else:
        c = p.cols["close"]
        prev = np.where(p.pos[sig] >= 5, c[np.maximum(sig - 5, 0)], np.nan)
        score = c[sig] / prev - 1  # 더 많이 빠질수록 작다 → 앞으로
    df = pd.DataFrame({"i": sig, "d": p.dates[sig], "s": score}).sort_values(["d", "s"])
    return df.groupby("d").head(n)["i"].to_numpy()


# ---------------------------------------------------------------- 본체
def run(dataset: str = "kr", log=print) -> Path:
    t0 = time.time()
    root = config.dataset_dir(dataset)
    log("데이터 읽는 중…")
    panel = Panel.load(root / "prices.parquet")
    runner = Runner(panel, {"name": dataset, "markets": ["KOSPI", "KOSDAQ"], "currency": "KRW"})
    L: list[str] = []
    L.append(f"2차 전략 연구 · {dt.date.today()} · 국내 (2010~) · 비용 차감 · 평균은 거래 1건당")
    L.append(f"(유동성 조건: {LIQ.replace('그리고 ', '').replace(' 그리고', ',')})")

    # A. 정밀 탐색
    log("A. 급락 반등 숫자 정밀 탐색 (54개 조합)…")
    grid = _family_spec(REBOUND_GRID, ["코스피", "코스닥"])
    ex = runner.explore(grid)
    cut = np.datetime64(ex["split"], "D")
    ctx = Ctx(runner, cut)
    L.append(f"과거 구간 ~{ex['split']} 이전 / 검증 구간 {ex['split']}~")
    L.append("")
    L.append(f"■ A. 급락 반등 정밀 탐색: {ex['n_total']}개 조합 중 통과 {ex['n_passed']}개 (두 구간 중 나쁜 쪽 평균 순)")
    top = ex["passed"][:10]
    for k, row in enumerate(top, 1):
        s = Spec.model_validate(row["spec"])
        i, o = row["is"], row["oos"]
        L.append(f"{k:>2}. 과거 {_fmt(i['avg_ret'])} ({i['n_complete']:,}건) · 검증 {_fmt(o['avg_ret'])} ({o['n_complete']:,}건)"
                 f" · 수익 난 해 {round((row['pos_years'] or 0) * 100)}%")
        L.append(f"    {s.signal.replace(LIQ, '').strip()} | {_params(s)}")
    eff = ex["axis_effects"]
    L.append("  숫자별 경향 (그 값을 쓴 조합들의 검증 구간 평균 중앙값):")
    for a in eff:
        vals = " / ".join(f"{v['value'] if v['value'] is not None else '없음'}: {_fmt(v['oos_avg'])}" for v in a["values"])
        L.append(f"   - {a['key']}: {vals}")
    log(f"   통과 {ex['n_passed']}개 ({time.time() - t0:.0f}초)")

    # B. 정밀 점검 (A 1위, 없으면 1차 추천 조합)
    base = Spec.model_validate(top[0]["spec"]) if top else _family_spec(REBOUND_BASE, ["코스피", "코스닥"])
    log("B. 정밀 점검…")
    L.append("")
    L.append("■ B. 정밀 점검 대상: " + base.signal.replace(LIQ, "").strip() + " | " + _params(base))
    sig = _signals(runner, base)
    t = _sim(runner, base, sig)
    allst = ctx.stats(t, base)
    L.append(ctx.line("전체", allst))

    # 1) 데이터 끊김
    dl = ctx.delisted(t)
    n_dl = int(dl.sum())
    done = (t.outcome_c != event.NO_FILL) & (t.outcome_c != event.OPEN)
    rets = t.ret_c[done]
    last_close = panel.cols["close"][panel.gend[t.sig[dl]]]
    dl_ret = (last_close / t.fill_price[dl] - 1) * 100
    adj_last = float(np.concatenate([rets, dl_ret]).mean()) if len(rets) + n_dl else None
    adj_worst = float(np.concatenate([rets, np.full(n_dl, DELIST_ASSUME)]).mean()) if len(rets) + n_dl else None
    L.append(f"  [상장폐지·장기 정지로 데이터 끊김] {n_dl}건 (전체 {len(rets):,}건 중 {n_dl / max(1, len(rets)) * 100:.2f}%)")
    L.append(f"    끊기기 전 마지막 가격으로 넣으면 평균 {_fmt(adj_last)} · {DELIST_ASSUME:.0f}% 손실로 넣으면 평균 {_fmt(adj_worst)}")

    # 2) 시장별
    L.append("  [시장별]")
    for mk, lab in (("KOSPI", "코스피"), ("KOSDAQ", "코스닥")):
        m = panel.markets[t.sig] == mk
        if m.any():
            L.append(ctx.line(lab, ctx.stats(t, base, m)))

    # 3) 시장 동반 급락 vs 개별 급락
    if ctx.idx:
        r5 = ctx.market_series(t.sig, "ret5")
        L.append("  [그날 지수의 5일 수익률]")
        for lab, m in (("지수도 -5% 이하 (시장 폭락)", r5 <= -5), ("지수 -5~-2%", (r5 > -5) & (r5 <= -2)),
                       ("지수 -2% 초과 (종목만 급락)", r5 > -2)):
            if m.any():
                L.append(ctx.line(lab, ctx.stats(t, base, m)))
        up = ctx.market_series(t.sig, "above60")
        L.append("  [지수 60일선 위/아래]")
        for lab, m in (("지수 60일선 위", up == 1), ("지수 60일선 아래", up == 0)):
            if m.any():
                L.append(ctx.line(lab, ctx.stats(t, base, m)))
    else:
        L.append("  (지수 데이터가 없어 시장 비교는 건너뜀)")

    # 4) 하루 n개만
    L.append("  [하루 신호가 많을 때 n개만 사면]")
    per_day = pd.Series(panel.dates[sig]).value_counts()
    L.append(f"    신호 있는 날 {len(per_day):,}일 · 하루 평균 {per_day.mean():.1f}개 · 최대 {per_day.max()}개 ({str(per_day.idxmax())[:10]})")
    for key, klab in (("value", "거래대금 큰 순"), ("drop", "더 많이 빠진 순")):
        for n in (1, 3, 5):
            sub = _cap(ctx, sig, n, key)
            tt = _sim(runner, base, sub)
            L.append(ctx.line(f"{klab} {n}개", ctx.stats(tt, base)))

    # 5) 연도별
    L.append("  [연도별]")
    years = panel.dates[t.sig].astype("datetime64[Y]").astype(int) + 1970
    row = []
    for y in np.unique(years):
        s = summarize(t, base, years == y)
        if s["n_complete"]:
            row.append(f"{y} {_fmt(s['avg_ret'], 1)}({s['n_complete']})")
    for k in range(0, len(row), 6):
        L.append("    " + "  ".join(row[k:k + 6]))

    # 6) 신호가 몰린 날
    L.append("  [신호가 가장 많았던 날 5개]")
    d_sig = panel.dates[t.sig]
    for d, cnt in per_day.head(5).items():
        m = d_sig == np.datetime64(d, "D")
        s = summarize(t, base, m)
        L.append(f"    {str(d)[:10]}: {cnt}개 · 평균 {_fmt(s['avg_ret'])}")
    top_days = per_day.head(10).index
    m_top = np.isin(d_sig, np.array(top_days, dtype="datetime64[D]"))
    if m_top.any():
        L.append(ctx.line("몰린 10일 빼면", ctx.stats(t, base, ~m_top)))
    log(f"   ({time.time() - t0:.0f}초)")

    # C. 52주 신고가 + 시장 필터
    log("C. 52주 신고가 + 시장 필터…")
    L.append("")
    L.append("■ C. 52주 신고가 추세: 지수가 60일선 위일 때만 사면")
    for name, text in HIGH_TREND:
        s = _family_spec(text, ["코스피", "코스닥"])
        sg = _signals(runner, s)
        tt = _sim(runner, s, sg)
        L.append(f"  {name} | {_params(s)}")
        L.append(ctx.line("필터 없음", ctx.stats(tt, s)))
        if ctx.idx:
            up = ctx.market_series(tt.sig, "above60")
            L.append(ctx.line("지수 60일선 위일 때만", ctx.stats(tt, s, up == 1)))
            L.append(ctx.line("지수 60일선 아래일 때", ctx.stats(tt, s, up == 0)))
        yrs = panel.dates[tt.sig].astype("datetime64[Y]").astype(int) + 1970
        pos = tot = 0
        for y in np.unique(yrs):
            ss = summarize(tt, s, yrs == y)
            if ss["n_complete"] >= 10 and ss["avg_ret"] is not None:
                tot += 1
                pos += ss["avg_ret"] > 0
        L.append(f"    수익 난 해 {pos}/{tot} · 데이터 끊김 {int(ctx.delisted(tt).sum())}건")

    L.append("")
    L.append(f"소요 {round((time.time() - t0) / 60, 1)}분")
    report = "\n".join(L)
    out_dir = config.DATA_DIR / "research"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"research2-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    log(f"\n보고서 파일: {path}")
    return path

