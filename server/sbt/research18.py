"""영업이익 시험: 이익이 크게·빨라지게 늘었다는 공시 뒤에 사면 주가가 계속 더 갈까.

  python -m sbt.data.cli profit-test     (먼저 dart-collect)

재무는 DART 정기보고서(분기·반기·사업)의 영업이익. 공시일 '다음 거래일 종가'에 산 것으로 친다.
대상은 코스피 보통주, 공시 전달 말 시총 순위로 '상위 100' 과 '101~300위' 를 따로 본다.

신호 (분기 영업이익, 전년 같은 분기 대비)
- 증익 30%+ : +30% 이상 (전년 같은 분기도 흑자)
- 증익 가속  : +30% 이상이고, 증가율이 바로 앞 분기보다 커짐
- 2분기 연속 가속 : 2분기 연속 +20% 이상이고 가속
- 흑자 전환  : 전년 같은 분기 적자 → 이번 흑자
- 이익률 개선 : 영업이익률이 전년 같은 분기보다 2%p 이상 높고 증익
- 가속 + 발표 반응 : 증익 가속 이면서 공시 전날 → 공시 2거래일 뒤 주가 +3% 이상 (그 뒤에 산다)
- (비교) 감익 30%+ : -30% 이하
1부. 신호 뒤 20·60·120·250거래일 수익 - 같은 그룹(상위 100 또는 101~300위) 평균 = 초과 수익.
2부. 신호마다 120거래일(약 6개월) 들고 가는 계좌 (그날 들고 있는 것들 똑같이 나눔, 없으면 현금).
주의: 큰 회사는 정기보고서 전에 '잠정 실적'을 먼저 공시해서, 정기보고서 날엔 반응이 이미 지나갔을 수 있다.
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import config
from .data import dart
from .data.indices import load as index_load
from .data.panel import Panel
from .research14 import PERIODS, kospi_frame

HOLD = 120
COST = 0.0025
HORIZONS = (20, 60, 120, 250)


def events(raw: pd.DataFrame) -> pd.DataFrame:
    """회사 × 분기 공시: 영업이익 증가율·가속·흑자 전환·이익률."""
    op = dart.quarterly(raw, dart.OP)
    rev = dart.quarterly(raw, dart.REVENUE)[["stock_code", "year", "q", "amount"]].rename(columns={"amount": "rev"})
    d = op.merge(rev, on=["stock_code", "year", "q"], how="left").rename(columns={"amount": "op"})
    d["fq"] = d["year"] * 4 + d["q"]
    out = []
    for code, g in d.groupby("stock_code", sort=False):
        g = g.set_index("fq").sort_index()
        full = g.reindex(range(int(g.index.min()), int(g.index.max()) + 1))
        op4, rev4 = full["op"].shift(4), full["rev"].shift(4)
        yoy = np.where(op4 > 0, full["op"] / op4 - 1, np.nan)
        yoy = pd.Series(yoy, index=full.index)
        m = full["op"] / full["rev"]
        m4 = op4 / rev4
        x = pd.DataFrame({"op": full["op"], "op4": op4, "yoy": yoy, "yoy_prev": yoy.shift(1),
                          "margin": m, "margin4": m4, "rcept": full["rcept"]})
        x = x.loc[g.index].dropna(subset=["op", "rcept"])
        x["code"] = code
        x["year"] = (x.index - 1) // 4
        out.append(x.reset_index())
    e = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    if e.empty:
        return e
    e["up30"] = e["yoy"] >= 0.30
    e["accel"] = e["up30"] & (e["yoy"] > e["yoy_prev"])
    e["accel2"] = (e["yoy"] >= 0.20) & (e["yoy_prev"] >= 0.20) & (e["yoy"] > e["yoy_prev"])
    e["turn"] = (e["op4"] <= 0) & (e["op"] > 0)
    e["margin_up"] = (e["margin"] - e["margin4"] >= 0.02) & (e["op"] > e["op4"])
    e["down30"] = e["yoy"] <= -0.30
    return e


def run(dataset: str = "kr", log=print) -> Path:
    t0 = time.time()
    raw = dart.load_raw()
    if raw.empty:
        raise SystemExit("DART 재무 자료가 없습니다. 연구 탭 'DART 재무 자료 받기'를 먼저 돌려 주세요.")
    log("영업이익 표 만드는 중…")
    ev = events(raw)
    log("주가 표 만드는 중…")
    panel = Panel.load(config.dataset_dir(dataset) / "prices.parquet")
    df = kospi_frame(panel, ("KOSPI",))
    R = df.pivot_table(index="date", columns="code", values="ret")
    RK = df.pivot_table(index="date", columns="code", values="rank").reindex(index=R.index, columns=R.columns)
    dates = R.index.to_numpy(dtype="datetime64[ns]")
    Rv = R.to_numpy()
    cum = np.vstack([np.zeros(R.shape[1]), np.cumsum(np.log1p(np.nan_to_num(Rv)), axis=0)])  # cum[i+1] = i일 종가까지
    col = {c: j for j, c in enumerate(R.columns)}
    groups = {"상위 100": (RK.to_numpy() <= 100), "101~300위": (RK.to_numpy() > 100) & (RK.to_numpy() <= 300)}
    bench = {}
    for g, mask in groups.items():
        b = np.where(mask, Rv, np.nan)
        cnt = np.isfinite(b).sum(axis=1)
        br = np.where(cnt > 0, np.nansum(b, axis=1) / np.maximum(cnt, 1), 0.0)
        bench[g] = (br, np.r_[0.0, np.cumsum(np.log1p(br))])

    ev = ev[ev["code"].isin(col)].copy()
    ev["rc"] = pd.to_datetime(ev["rcept"]).to_numpy(dtype="datetime64[ns]")
    ev["t0"] = np.searchsorted(dates, ev["rc"].to_numpy(), side="right")  # 공시 다음 거래일
    ev = ev[ev["t0"] < len(dates) - 1]
    jj = ev["code"].map(col).to_numpy()
    t0s = ev["t0"].to_numpy()
    before = np.maximum(np.searchsorted(dates, ev["rc"].to_numpy(), side="left") - 1, 0)
    t3 = np.minimum(t0s + 1, len(dates) - 1)  # 공시 2거래일 뒤
    ev["react"] = np.exp(cum[t3 + 1, jj] - cum[before + 1, jj]) - 1
    rk = RK.to_numpy()[t0s, jj]
    ev["grp"] = np.where(rk <= 100, "상위 100", np.where((rk > 100) & (rk <= 300), "101~300위", None))
    ev = ev[ev["grp"].notna()]
    ev["accel_react"] = ev["accel"] & (ev["react"] >= 0.03)
    signals = [("모든 정기 공시 (기준)", None), ("증익 30%+", "up30"), ("증익 가속", "accel"), ("2분기 연속 가속", "accel2"),
               ("흑자 전환", "turn"), ("이익률 개선", "margin_up"), ("가속 + 발표 반응 +3%", "accel_react"),
               ("(비교) 감익 30%+", "down30")]

    def entry(e: pd.DataFrame, key: str | None) -> np.ndarray:
        return np.minimum(e["t0"].to_numpy() + (1 if key == "accel_react" else 0), len(dates) - 1)

    L = [f"영업이익 시험 · {dt.date.today()} · 주가 ~{R.index[-1].date()} · 재무 {int(ev['year'].min())}~{int(ev['year'].max())} (DART 정기보고서)",
         "공시 다음 거래일 종가에 산 것으로 계산 · 초과 = 같은 그룹(상위 100 또는 101~300위) 동일 비중 평균 대비 · 배당 제외", ""]
    L += ["1부. 신호 뒤 초과 수익 (평균, 괄호 = 초과가 플러스인 비율)"]
    for g in groups:
        L += ["", f"  ■ {g}",
              f"  {'':<24} {'건수':>6}" + "".join(f"{f'{h}일 뒤':>14}" for h in HORIZONS)]
        bc = bench[g][1]
        for label, key in signals:
            e = ev[(ev["grp"] == g) & (True if key is None else ev[key].fillna(False))]
            if e.empty:
                L.append(f"  {label:<24} {0:>6}")
                continue
            t = entry(e, key)
            j = e["code"].map(col).to_numpy()
            cells = []
            for h in HORIZONS:
                ok = t + h < len(dates)
                if not ok.any():
                    cells.append("-")
                    continue
                s = np.exp(cum[t[ok] + h + 1, j[ok]] - cum[t[ok] + 1, j[ok]]) - 1
                b = np.exp(bc[t[ok] + h + 1] - bc[t[ok] + 1]) - 1
                x = s - b
                cells.append(f"{x.mean() * 100:+.1f}%({(x > 0).mean() * 100:.0f}%)")
            L.append(f"  {label:<24} {len(e):>6,}" + "".join(f"{c:>14}" for c in cells))

    # 해마다 (가장 많이 보는 신호: 증익 가속, 120일)
    L += ["", "  해마다 '증익 가속' 120일 초과 (상위 100 / 101~300위, 건수)"]
    row = []
    for y in sorted(ev["year"].unique()):
        cells = []
        for g in groups:
            e = ev[(ev["grp"] == g) & ev["accel"].fillna(False) & (ev["year"] == y)]
            t = entry(e, "accel")
            ok = t + 120 < len(dates)
            if not ok.any():
                cells.append("-")
                continue
            j = e["code"].map(col).to_numpy()[ok]
            x = (np.exp(cum[t[ok] + 121, j] - cum[t[ok] + 1, j]) - 1) - (np.exp(bench[g][1][t[ok] + 121] - bench[g][1][t[ok] + 1]) - 1)
            cells.append(f"{x.mean() * 100:+.0f}%({ok.sum()})")
        row.append(f"{int(y)} " + "/".join(cells))
    for k in range(0, len(row), 4):
        L.append("    " + "   ".join(row[k:k + 4]))

    # 2부: 계좌
    log("계좌로 굴리기…")
    L += ["", f"2부. 신호마다 {HOLD}거래일 들고 가는 계좌 (그날 들고 있는 것들 똑같이 나눔, 없으면 현금, 사고팔 때 {COST * 100:.2f}%)",
          f"  {'':<34} {'배수':>6} {'연':>7} {'최대 낙폭':>8} {'평균 종목':>7} {'투자':>5}" + "".join(f"{n[:10]:>13}" for n, _, _ in PERIODS[2:-1])]
    start = int(ev["t0"].min()) if len(ev) else 0

    def line(label: str, r: np.ndarray, n_avg: float | None, inv: float | None) -> str:
        s = pd.Series(r[start:], index=R.index[start:])
        eq = (1 + s).cumprod()
        yrs = max((s.index[-1] - s.index[0]).days / 365.25, 0.5)
        cells = []
        for _, a_, b_ in PERIODS[2:-1]:
            seg = s[(s.index >= a_) & (s.index <= b_)]
            cells.append(f"{((1 + seg).prod() - 1) * 100:+.0f}%" if len(seg) else "-")
        extra = f"{n_avg:>7.0f} {inv * 100:>4.0f}%" if n_avg is not None else f"{'':>7} {'':>5}"
        return (f"  {label:<34} {eq.iloc[-1]:>5.1f}배 {(eq.iloc[-1] ** (1 / yrs) - 1) * 100:>+6.1f}% "
                f"{(eq / eq.cummax() - 1).min() * 100:>7.1f}% {extra}" + "".join(f"{c:>13}" for c in cells))

    for g in groups:
        L.append(line(f"{g} 평균 (기준)", bench[g][0], None, None))
        for label, key in signals[1:-1]:
            e = ev[(ev["grp"] == g) & ev[key].fillna(False)]
            if e.empty:
                continue
            act = np.zeros_like(Rv)
            t = entry(e, key)
            j = e["code"].map(col).to_numpy()
            buys = np.zeros(len(dates))
            for ti, ji in zip(t, j):
                act[ti + 1:ti + 1 + HOLD, ji] = 1.0
                buys[ti] += 1
            n = act.sum(axis=1)
            r = np.where(n > 0, np.nansum(act * np.nan_to_num(Rv), axis=1) / np.maximum(n, 1), 0.0)
            prev_n = np.r_[0.0, n[:-1]]
            r = r - COST * np.where(prev_n > 0, buys / np.maximum(prev_n + buys, 1), np.where(buys > 0, 1.0, 0.0))
            L.append(line(f"  {label}", r, float(n[n > 0].mean()) if (n > 0).any() else 0.0,
                          float((n[start:] > 0).mean())))
        L.append("")
    k = index_load("KOSPI")
    if k is not None:
        ks = k[(k.index >= R.index[start]) & (k.index <= R.index[-1])]
        if len(ks) > 1:
            L.append(f"  (같은 기간 코스피 지수 {ks.iloc[-1] / ks.iloc[0]:.1f}배)")

    # 최근 신호
    names = dict(zip(panel.codes.astype(str), panel.names))
    recent = ev[(ev["accel"].fillna(False)) & (ev["rc"] >= np.datetime64(R.index[-1] - pd.Timedelta(days=90)))]
    recent = recent.sort_values("rc", ascending=False)
    L += ["", f"최근 90일 '증익 가속' 공시 {len(recent)}건 (공시일 · 그룹 · 분기 영업이익 전년 대비 · 앞 분기 · 발표 반응):"]
    for r in recent.head(25).itertuples():
        L.append(f"  {pd.Timestamp(r.rc).date()} {names.get(r.code, r.code):<14} {r.grp:<8} {r.yoy * 100:+.0f}% "
                 f"(앞 분기 {'-' if pd.isna(r.yoy_prev) else f'{r.yoy_prev * 100:+.0f}%'}) · 반응 {r.react * 100:+.1f}%")
    L += ["", "읽는 법: 1부 초과가 꾸준히 플러스(괄호 55% 이상)이고 250일까지 이어지면 '발표 뒤에 사도 늦지 않은' 신호.",
          "2부에서 기준(그룹 평균)보다 연 수익이 높고 낙폭이 비슷하면 계좌로 쓸 만함.",
          "주의: 정기보고서 공시일 기준. 잠정 실적은 보통 더 일찍 나와서 실제론 더 빨리 반응할 수 있음. 재무 2015~ 라 약 10년치.",
          f"소요 {round((time.time() - t0) / 60, 1)}분"]
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"profit-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
