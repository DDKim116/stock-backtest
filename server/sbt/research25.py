"""실적 가속을 더 빨리: 정기보고서 대신 '잠정 실적' 공시 날에 사면 얼마나 나아질까.

  python -m sbt.data.cli accel-fast     (먼저 dart-collect 와 dart-events)

신호는 accel-plan 과 같음: 코스피 101~300위 · 2분기 연속 영업이익 +20% 이상 가속.
그 분기 끝난 뒤 ~ 정기보고서 공시 사이에 그 회사의 '잠정 실적' 공시가 있으면 그 날(첫 번째)로 당긴다.
1부. 잠정 공시가 있는 신호만: 정기보고서 날 vs 잠정 날 산 것의 초과 수익 (같은 101~300위 평균 대비).
2부. accel-plan 계좌 (1억 · 최대 30종목 · 6개월): 정기보고서 날 vs 잠정 날(있으면).
주의: 잠정 숫자는 정기보고서 숫자와 조금 다를 수 있다 (여기선 정기보고서 숫자로 신호를 판단 → 아주 조금 미리 아는 셈).
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import config
from .data import dart, dart_events
from .data.panel import Panel
from .research18 import HORIZONS, events
from .research19 import Market, accel_signals, simulate


def quarter_end(fq: pd.Series) -> pd.Series:
    year = (fq - 1) // 4
    q = fq - year * 4
    return pd.to_datetime(pd.DataFrame({"year": year, "month": q * 3, "day": 1})) + pd.offsets.MonthEnd(0)


def attach_prelim(ev: pd.DataFrame, pre: pd.DataFrame) -> pd.DataFrame:
    """ev 에 prelim(그 분기 끝 ~ 정기보고서 사이 첫 잠정 공시 날)을 붙인다. 없으면 NaT."""
    e = ev.copy()
    e["rcept"] = pd.to_datetime(e["rcept"])
    e["qend"] = quarter_end(e["fq"])
    e["prelim"] = pd.NaT
    if pre.empty:
        return e
    by = {c: np.sort(g["date"].to_numpy(dtype="datetime64[ns]")) for c, g in pre.groupby("code")}
    out = []
    for code, qe, rc in zip(e["code"], e["qend"].to_numpy(dtype="datetime64[ns]"), e["rcept"].to_numpy(dtype="datetime64[ns]")):
        d = by.get(code)
        if d is None:
            out.append(np.datetime64("NaT"))
            continue
        k = np.searchsorted(d, qe, side="right")
        out.append(d[k] if k < len(d) and d[k] <= rc else np.datetime64("NaT"))
    e["prelim"] = pd.to_datetime(np.array(out, dtype="datetime64[ns]"))
    return e


def run(dataset: str = "kr", log=print) -> Path:
    t0 = time.time()
    raw = dart.load_raw()
    pre = dart_events.load("prelim")
    if raw.empty:
        raise SystemExit("DART 재무 자료가 없습니다. 연구 탭 'DART 재무 자료 받기'를 먼저 돌려 주세요.")
    if pre.empty:
        raise SystemExit("잠정 실적 공시 자료가 없습니다. 연구 탭 'DART 공시 사건 받기'를 먼저 돌려 주세요.")
    log("데이터 읽는 중…")
    panel = Panel.load(config.dataset_dir(dataset) / "prices.parquet")
    mk = Market(panel)
    ev = attach_prelim(events(raw), pre)
    fast = ev.copy()
    has = fast["prelim"].notna()
    fast.loc[has, "rcept"] = fast.loc[has, "prelim"]

    # 101~300위 동일 비중 기준
    RK = mk.RK
    grp = (RK > 100) & (RK <= 300)
    b = np.where(grp, mk.R.to_numpy(), np.nan)
    cnt = np.isfinite(b).sum(axis=1)
    br = np.where(cnt > 0, np.nansum(b, axis=1) / np.maximum(cnt, 1), 0.0)
    bc = np.r_[0.0, np.cumsum(np.log1p(br))]
    cum = np.vstack([np.zeros(mk.Rv.shape[1]), np.cumsum(np.log1p(mk.Rv), axis=0)])

    def study(sig: dict[int, list[int]]) -> list[str]:
        t = np.array([k for k, js in sig.items() for _ in js], dtype=int)
        j = np.array([x for js in sig.values() for x in js], dtype=int)
        cells = []
        for h in HORIZONS:
            ok = t + h < len(mk.dates)
            if not ok.any():
                cells.append("-")
                continue
            x = (np.exp(cum[t[ok] + h + 1, j[ok]] - cum[t[ok] + 1, j[ok]]) - 1) - (np.exp(bc[t[ok] + h + 1] - bc[t[ok] + 1]) - 1)
            cells.append(f"{x.mean() * 100:+.1f}%({(x > 0).mean() * 100:.0f}%)")
        return [f"{len(t):>6,}"] + cells

    a2 = ev["accel2"].fillna(False)
    n_all = int(a2.sum())
    n_pre = int((a2 & has).sum())
    lead = (ev.loc[a2 & has, "rcept"] - ev.loc[a2 & has, "prelim"]).dt.days
    L = [f"실적 가속을 더 빨리 (잠정 실적) · {dt.date.today()} · 주가 ~{mk.days[-1].date()} · 코스피 101~300위 · 2분기 연속 가속",
         f"신호(코스피 전체) {n_all:,}건 중 잠정 공시가 먼저 나온 것 {n_pre:,}건 ({n_pre / max(n_all, 1) * 100:.0f}%) · "
         f"앞당겨진 날 중간값 {lead.median() if len(lead) else 0:.0f}일 (평균 {lead.mean() if len(lead) else 0:.0f}일)", "",
         "1부. 잠정 공시가 있는 신호만 · 산 날을 바꾸면 (101~300위 평균 대비 초과, 괄호 = 플러스 비율)",
         f"  {'':<28} {'건수':>6}" + "".join(f"{f'{h}일 뒤':>14}" for h in HORIZONS)]
    sub_p = accel_signals(mk, ev[has], "accel2")
    sub_f = accel_signals(mk, fast[has], "accel2")
    for label, sig in (("정기보고서 다음 날 종가", sub_p), ("잠정 공시 다음 날 종가", sub_f),
                       ("(참고) 잠정 없는 신호", accel_signals(mk, ev[~has], "accel2"))):
        c = study(sig)
        L.append(f"  {label:<28} {c[0]}" + "".join(f"{x:>14}" for x in c[1:]))

    log("계좌 굴리기…")
    s_per = accel_signals(mk, ev, "accel2")
    s_fast = accel_signals(mk, fast, "accel2")
    start = min(min(s_per, default=0), min(s_fast, default=0))
    cap = 100_000_000
    L += ["", "2부. 1억 · 최대 30종목 · 6개월 (accel-plan 에서 고른 규칙)",
          f"  {'':<28} {'배수':>6} {'연':>7} {'최대 낙폭':>8} {'투자':>5} {'산 것':>6} {'건너뜀':>6}"]
    for label, sig in (("정기보고서 날 (지금)", s_per), ("잠정 날 (있으면)", s_fast)):
        for hold in (120, 250):
            r = simulate(mk, cap, sig, None, start=start, max_n=30, hold=hold)
            L.append(f"  {label + (' · 1년' if hold == 250 else ''):<28} {r['final'] / cap:>5.1f}배 {r['cagr']:>+6.1f}% "
                     f"{r['mdd']:>7.1f}% {r['inv']:>4.0f}% {r['acc']:>6,} {r['acc_skip']:>6,}")

    names = dict(zip(panel.codes.astype(str), panel.names))
    rec = fast[a2 & has & (fast["rcept"] >= mk.days[-1] - pd.Timedelta(days=120))].sort_values("rcept", ascending=False)
    L += ["", f"최근 120일 잠정 공시로 잡힌 가속 신호 {len(rec)}건 (코스피 전체, 101~300위 여부는 사는 날 기준):"]
    for r in rec.head(20).itertuples():
        L.append(f"  잠정 {r.prelim.date()} (정기 {pd.Timestamp(ev.loc[r.Index, 'rcept']).date()}) {names.get(r.code, r.code):<14} "
                 f"영업이익 {r.yoy * 100:+.0f}% (앞 분기 {r.yoy_prev * 100:+.0f}%)")
    L += ["", "읽는 법: 1부에서 잠정 날이 정기보고서 날보다 초과가 크면 '빨리 사는 게' 이득 → 잠정 공시를 매일 확인할 이유가 있음.",
          "둘이 비슷하면 지금처럼 정기보고서 뒤에 사도 됨 (발표 뒤에도 오래 가는 신호라는 뜻).",
          "잠정 공시는 큰 회사 위주라 101~300위 중 일부만 해당. 코스피 회사만 받음.",
          f"소요 {round((time.time() - t0) / 60, 1)}분"]
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"accel-fast-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
