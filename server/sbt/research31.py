"""코스피 시총 30위권 급락 × 공포 구간: 시장이 겁먹었을 때 대형주 급락 다음 날 사서 며칠 들면 남나.

  python -m sbt.data.cli top30-fear

대상: top30-patterns 와 같음 (전달 말 코스피 시총 1~30위, 삼성전자·SK하이닉스 제외).
신호: 그날 -4%(또는 -3%·-6%) 이상 급락 → 다음 날 시가 매수. 비용: 살 때 0.015%, 팔 때 0.215%.
공포 구간: 그날의 '국장 공포·탐욕' 점수 (앱 홈과 같은 계산 — 모멘텀·시장 폭·신고가-신저가·변동성·고점 대비, 그날까지 자료만.
  과거가 길도록 VKOSPI 대신 코스피 20일 실제 변동성 사용). 25 미만 = 극단적 공포, 25~45 = 공포.
  그 밖에 '코스피도 그날 -2%↓', '코스피 1년 고점 대비 -15%↓'.

중요: 공포 구간엔 시장 전체가 반등하기 쉽다 → '같은 구간의 아무 날'과 비교해야 급락 종목 덕인지 안다.
t값은 같은 날 신호를 하루 평균으로 묶어 계산 (같은 날 여러 종목이 같이 움직이는 걸 감안한 보수적인 값).
1부. 구간별 '아무 날' (시장 자체가 공포 뒤에 얼마나 오르나)
2부. -4% 급락 × 구간: 1·3·5·10일 보유
3부. 급락 크기(-3·-4·-6%) × 구간: 5일 보유만
4부. 가장 나은 조합을 해마다 (몇 번의 사건에 기대는지)
5부. 목표·손절 규칙 · 계좌 (5천만 · 1회 20% · 하루 최대 3종목, 많이 빠진 순)
6부. 지금 점수 · 최근 신호
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import breadth, config
from .data import indices
from .data.panel import Panel
from .pulse import fear_series
from .research10 import simulate as account
from .research30 import CUT, RULES, Book, _feat, universe

HOLDS = [(1, "1일"), (3, "3일"), (5, "5일"), (10, "10일")]


def _ct(ret: np.ndarray, base: float, dates: np.ndarray) -> float:
    """같은 날 신호를 하루 평균으로 묶은 t값."""
    ok = np.isfinite(ret)
    if ok.sum() < 10:
        return float("nan")
    s = pd.Series(ret[ok] - base).groupby(dates[ok]).mean()
    if len(s) < 5 or s.std(ddof=1) == 0:
        return float("nan")
    return float(s.mean() / (s.std(ddof=1) / np.sqrt(len(s))))


def _m(x: np.ndarray) -> float:
    x = x[np.isfinite(x)]
    return float(x.mean()) if len(x) else float("nan")


def run(dataset: str = "kr", log=print) -> Path:
    t0 = time.time()
    log("데이터 읽는 중…")
    panel = Panel.load(config.dataset_dir(dataset) / "prices.parquet")
    if "mktcap" not in panel.cols:
        raise SystemExit("시가총액 자료가 없습니다.")
    breadth.attach(panel)
    log("공포·탐욕 점수 (과거 전체)…")
    fear = fear_series(panel)
    k = indices.load("KOSPI")
    if fear.empty or k is None:
        raise SystemExit("코스피 지수·시장 폭 자료가 없습니다 (국내 갱신을 먼저).")
    kret = k.pct_change()
    kdd = k / k.rolling(250, min_periods=120).max() - 1

    uni = universe(panel)
    codes = np.unique(panel.codes[uni].astype(str))
    rows = np.flatnonzero(np.isin(panel.codes.astype(str), codes))
    log(f"대상 {len(codes)}종목 · 계산…")
    df = _feat(panel, rows)
    in_uni = uni[rows]
    chg_all = df["chg"].to_numpy()

    def at(series: pd.Series, rr: np.ndarray) -> np.ndarray:
        d = panel.dates[rr]
        u = np.unique(d)
        ui = pd.DatetimeIndex(u)
        v = series[~series.index.duplicated()].sort_index()
        vals = v.reindex(v.index.union(ui)).ffill().reindex(ui).to_numpy()
        return vals[np.searchsorted(u, d)]

    # 비교 기준: 대상인 날 전부
    bb = Book(panel, rows[in_uni])
    b_fear, b_kret, b_kdd = at(fear, bb.sig), at(kret, bb.sig), at(kdd, bb.sig)
    b_ret = {h: bb.hold(h) for h, _ in HOLDS}

    # 신호: -3% 이상 급락 (그 안에서 -4·-6 로 나눔)
    sig_rows = rows[in_uni & (chg_all <= -0.03)]
    bk = Book(panel, sig_rows)
    pos = np.searchsorted(rows, bk.sig)
    chg = chg_all[pos]
    f_, kr_, kd_ = at(fear, bk.sig), at(kret, bk.sig), at(kdd, bk.sig)
    sdate = panel.dates[bk.sig]
    ret = {h: bk.hold(h) for h, _ in HOLDS}

    def regimes(fv, kv, dv):
        with np.errstate(invalid="ignore"):
            return [("전체", np.ones(len(fv), bool)),
                    ("점수 15 미만", fv < 15),
                    ("극단적 공포 (25 미만)", fv < 25),
                    ("공포 (25~45)", (fv >= 25) & (fv < 45)),
                    ("중립 이상 (45 이상)", fv >= 45),
                    ("코스피도 그날 -2%↓", kv <= -0.02),
                    ("극단적 공포 + 코스피 -2%↓", (fv < 25) & (kv <= -0.02)),
                    ("코스피 1년 고점 대비 -15%↓", dv <= -0.15)]

    breg = dict(regimes(b_fear, b_kret, b_kdd))
    sreg = regimes(f_, kr_, kd_)
    yrs = max(len(np.unique(pd.DatetimeIndex(sdate).year)), 1)

    L = [f"코스피 시총 30위권 급락 × 공포 구간 · {dt.date.today()} · 주가 ~{pd.Timestamp(panel.dates.max()).date()}",
         f"대상 {len(codes)}종목 거쳐 감 (삼성전자·SK하이닉스 제외) · 급락 다음 날 시가 매수 · 비용 차감 · 과거 ~2020 / 검증 2021~",
         f"공포·탐욕 점수 계산 가능 기간 {fear.index[0].date()} ~ {fear.index[-1].date()} (그 전 신호는 '전체'에만 들어감)", ""]

    # 1부
    L += ["1부. 구간별 '아무 날' (같은 28종목을 그 구간 아무 날이나 샀을 때) — 시장 자체의 반등",
          f"  {'':<26}{'종목·일':>9}" + "".join(f"{lab:>8}" for _, lab in HOLDS)]
    base_by = {}
    for name, m in breg.items():
        base_by[name] = {h: _m(b_ret[h][m]) for h, _ in HOLDS}
        base_by[name]["pa"] = {h: _m(b_ret[h][m & bb.past]) for h, _ in HOLDS}
        base_by[name]["va"] = {h: _m(b_ret[h][m & ~bb.past]) for h, _ in HOLDS}
        L.append(f"  {name:<26}{int(m.sum()):>9,}" + "".join(f"{base_by[name][h]:>+7.2f}%" for h, _ in HOLDS))
    L.append("")

    # 2부
    d4 = chg <= -0.04
    L += ["2부. -4% 이상 급락 × 구간 (평균 = 비용 뺀 1건 수익 · '대비' = 같은 구간 아무 날과의 차이)"]
    best = []
    for name, m in sreg:
        mm = m & d4
        n = int(mm.sum())
        if n < 15:
            L += [f"  ■ {name}: {n}건 — 너무 적음", ""]
            continue
        nd = len(np.unique(sdate[mm]))
        b = base_by[name]
        L += [f"  ■ {name} · {n:,}건 · 신호 있던 날 {nd:,}일 (한 해 약 {n / yrs:.0f}건)",
              f"    {'보유':<10}" + "".join(f"{lab:>8}" for _, lab in HOLDS),
              f"    {'평균':<10}" + "".join(f"{_m(ret[h][mm]):>+7.2f}%" for h, _ in HOLDS),
              f"    {'이긴 비율':<10}" + "".join(f"{(ret[h][mm & np.isfinite(ret[h])] > 0).mean() * 100:>7.0f}%" for h, _ in HOLDS),
              f"    {'대비':<10}" + "".join(f"{_m(ret[h][mm]) - b[h]:>+7.2f}%" for h, _ in HOLDS),
              f"    {'t값(날 묶음)':<9}" + "".join(f"{_ct(ret[h][mm], b[h], sdate[mm]):>8.1f}" for h, _ in HOLDS),
              f"    {'과거 대비':<9}" + "".join(f"{_m(ret[h][mm & bk.past]) - b['pa'][h]:>+7.2f}%" for h, _ in HOLDS),
              f"    {'검증 대비':<9}" + "".join(f"{_m(ret[h][mm & ~bk.past]) - b['va'][h]:>+7.2f}%" for h, _ in HOLDS), ""]
        best.append((name, mm))

    # 3부
    L += ["3부. 급락 크기 × 구간 · 5일 보유 (평균 / 대비 / t / 과거 대비 / 검증 대비 / 건수)"]
    for lab, th in (("-3%↓", -0.03), ("-4%↓", -0.04), ("-6%↓", -0.06)):
        L.append(f"  [{lab}]")
        for name, m in sreg:
            mm = m & (chg <= th)
            if mm.sum() < 15:
                continue
            b = base_by[name]
            r = ret[5]
            L.append(f"    {name:<26}{_m(r[mm]):>+6.2f}% {_m(r[mm]) - b[5]:>+6.2f}%p t {_ct(r[mm], b[5], sdate[mm]):>4.1f}"
                     f"  과거 {_m(r[mm & bk.past]) - b['pa'][5]:>+5.2f} 검증 {_m(r[mm & ~bk.past]) - b['va'][5]:>+5.2f}  {int(mm.sum()):>5,}건")
    L.append("")

    # 4부: 해마다
    focus = [(n_, m) for n_, m in best if n_ in ("극단적 공포 (25 미만)", "점수 15 미만", "극단적 공포 + 코스피 -2%↓")]
    L += ["4부. 해마다 (-4% 급락 · 5일 보유 · 건수 / 날수 / 평균 / 같은 해 같은 구간 아무 날)"]
    by = pd.DatetimeIndex(sdate).year
    bby = pd.DatetimeIndex(panel.dates[bb.sig]).year
    for name, mm in focus:
        L.append(f"  ■ {name}")
        bm = breg[name]
        for y in sorted(set(by[mm])):
            ym = mm & (by == y)
            L.append(f"    {y}  {int(ym.sum()):>4}건 {len(np.unique(sdate[ym])):>3}일  {_m(ret[5][ym]):>+6.2f}%  "
                     f"(아무 날 {_m(b_ret[5][bm & (bby == y)]):>+5.2f}%)")
    L.append("")

    # 5부: 규칙 · 계좌
    log("규칙 · 계좌…")
    days = np.unique(panel.dates)
    L += ["5부. 목표·손절 규칙 (-4% 급락 · 1건 평균 · 이긴 비율 · 과거/검증)"]
    rule_sets = [("5일 보유", None, None, 5)] + [(lab, t, s, hd) for lab, t, s, hd in RULES]
    acct_rows = []
    for name, mm in focus + [(n_, m) for n_, m in best if n_ == "전체"]:
        L.append(f"  ■ {name}")
        for lab, t, s, hd in rule_sets:
            if t is None:
                r = ret[5].copy()
                day = np.full(len(r), 5)
            else:
                x = bk.rule(t, s, hd)
                r, day = x["ret"], x["day"]
            r = np.where(mm, r, np.nan)
            L.append(f"    {lab:<22}{_m(r):>+7.2f}%{(r[np.isfinite(r)] > 0).mean() * 100:>6.0f}%"
                     f"  과거 {_m(r[bk.past]):>+5.2f} 검증 {_m(r[~bk.past]):>+5.2f}")
            if lab in ("5일 보유", "목표5 · 손절3 · 10일", "목표10 · 손절5 · 20일"):
                g = bk.leg(r, day)
                g.value = chg[np.flatnonzero(np.isfinite(r))]  # 많이 빠진 순으로
                a = account(g, days, 50_000_000, None, 0.20, 3, 20_000_000)
                acct_rows.append((f"{name} · {lab}", a))
        L.append("")
    L += ["  계좌 5천만 · 1회 계좌 20%(최대 2천만) · 하루 최대 3종목 · 실현 손익 기준",
          f"  {'':<40}{'최종':>8}{'연':>8}{'2021~':>8}{'최대 낙폭':>9}{'산 것':>7}"]
    for lab, a in acct_rows:
        L.append(f"  {lab[:40]:<40}{a['final'] / 1e8:>6.2f}억{a['cagr']:>+7.1f}%{a['oos_cagr'] or 0:>+7.1f}%{a['mdd']:>8.1f}%{a['taken']:>7,}")
    L.append("  (신호가 공포 구간에만 나와 몇 년씩 쉬는 전략 → 연 수익은 '노는 돈'까지 포함한 숫자)")

    # 6부
    names = dict(zip(panel.codes.astype(str), panel.names))
    last_days = np.unique(panel.dates[rows])[-10:]
    L += ["", f"6부. 지금: 공포·탐욕 점수 {fear.iloc[-1]:.0f} ({fear.index[-1].date()}, 실제 변동성 기준 — 앱 홈은 VKOSPI 기준이라 조금 다를 수 있음)",
          "  최근 10거래일 -4% 급락 (신호 날 · 종목 · 그날 등락 · 그날 점수 · 코스피)"]
    m = in_uni & (chg_all <= -0.04) & np.isin(panel.dates[rows], last_days)
    rr = rows[m]
    fv, kv = at(fear, rr), at(kret, rr)
    rec = sorted(zip(panel.dates[rr], [names.get(str(c), str(c)) for c in panel.codes[rr]], chg_all[m], fv, kv),
                 key=lambda z: (z[0], z[2]), reverse=True)
    L += [f"  {pd.Timestamp(d).date()} {nm:<12} {c * 100:+5.1f}%  점수 {f:>3.0f}  코스피 {kx * 100:+.1f}%" for d, nm, c, f, kx in rec[:30]] \
        or ["  없음"]
    L += ["", "읽는 법: 2·3부에서 '대비'가 과거·검증 모두 플러스이고 t값(날 묶음)이 2 근처 이상이면 공포 구간 대형주 급락 반등이 진짜.",
          "4부에서 한두 해(예: 2020년 3월)만 크게 벌고 나머지는 비슷하거나 손해면 '몇 번의 큰 사건'에 기댄 결과 → 조심.",
          "1부에서 공포 구간 '아무 날'도 많이 오르면, 급락 종목을 고르기보다 그냥 그때 지수(ETF)를 사는 것과 비교해 볼 것.",
          f"소요 {round((time.time() - t0) / 60, 1)}분"]
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"top30-fear-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
