"""매출이 꾸준히 느는 코스피 상위 100 회사: 주가와의 관계, 언제 사고 팔지.

  python -m sbt.data.cli growth-test     (먼저 dart-collect)

재무는 DART 주요 계정(2015~). 실적은 '공시일'부터 안 것으로 친다 (미래 정보를 미리 쓰지 않게).
대상은 매달 말 코스피 보통주 시총 상위 100 (그때그때의 상위 100, 상장폐지 종목 포함).

1부. 매출 증가와 주가: 그해 매출 증가율 구간별로 같은 해 주가, 사업보고서 공시 뒤 1년(4월~다음 3월) 주가.
2부. 꾸준히 성장한 회사를 샀다면: 3년 연속 매출 증가 등 그룹별로 그 뒤 1·2·3년 수익 (상위 100 평균 대비).
3부. 언제 살까: 꾸준 성장 회사 중 '매출 대비 주가(PSR)가 자기 5년 중간값보다 쌀 때' 등 조건별 월 리밸런싱 계좌.
4부. 언제 팔까: 꾸준 성장 + 쌀 때 산 뒤 파는 규칙별 거래 성적 (같은 기간 상위 100 평균 대비).
배당 제외, 비용: 계좌 리밸런싱은 바뀐 비중 × 0.25%, 거래는 0.25%.
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

COST = 0.0025


def fundamentals(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(분기 TTM 표, 연간 표). 둘 다 공시일(rcept) 기준으로 '그때 알 수 있던 값'을 붙일 수 있게."""
    q = dart.quarterly(raw)
    if q.empty:
        return q, q
    q = q.sort_values(["stock_code", "year", "q"]).copy()
    q["fq"] = q["year"] * 4 + q["q"]
    out = []
    for code, g in q.groupby("stock_code", sort=False):
        g = g.set_index("fq")
        amt = g["amount"]
        full = amt.reindex(range(int(g.index.min()), int(g.index.max()) + 1))
        ttm = full.rolling(4, min_periods=4).sum()
        yoy = ttm / ttm.shift(4) - 1
        gg = g.assign(ttm=ttm.reindex(g.index), ttm_yoy=yoy.reindex(g.index)).reset_index()
        out.append(gg)
    qt = pd.concat(out, ignore_index=True)
    an = q[q["q"] == 4][["stock_code", "year", "cum", "rcept"]].rename(columns={"cum": "rev"}).copy()
    an = an.sort_values(["stock_code", "year"])
    an["yoy"] = an.groupby("stock_code")["rev"].pct_change()
    an.loc[an["year"].diff().ne(1) & an["stock_code"].eq(an["stock_code"].shift()), "yoy"] = np.nan
    an["up3"] = an.groupby("stock_code")["yoy"].transform(lambda s: (s > 0).astype(float).where(s.notna()).rolling(3).sum())
    an["cagr3"] = an.groupby("stock_code")["rev"].transform(lambda s: (s / s.shift(3)) ** (1 / 3) - 1)
    return qt, an


def _asof(left: pd.DataFrame, right: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """left(date, code) 에 right(rcept, stock_code) 중 그날까지 공시된 가장 최근 값."""
    r = right.dropna(subset=["rcept"]).rename(columns={"stock_code": "code"}).copy()
    r["rcept"] = pd.to_datetime(r["rcept"]).astype("datetime64[ns]")
    r = r.sort_values("rcept")
    lft = left.copy()
    lft["date"] = pd.to_datetime(lft["date"]).astype("datetime64[ns]")
    lft = lft.sort_values("date")
    m = pd.merge_asof(lft, r[["code", "rcept"] + cols], left_on="date", right_on="rcept", by="code", direction="backward")
    return m


def monthly(panel: Panel) -> tuple[pd.DataFrame, pd.DataFrame]:
    """월말 표(종목 × 월: 순위·시총·종가·200일선·52주 고가)와 월 수익률 표(월 × 종목)."""
    df = kospi_frame(panel, ("KOSPI",))
    df["close"] = panel.cols["close"][df["row"].to_numpy()]
    d = df.dropna(subset=["ret"])
    mr = ((1 + d["ret"]).groupby([d["code"], d["ym"]]).prod() - 1).unstack("code").sort_index()
    C = df.pivot_table(index="date", columns="code", values="close")
    ma200 = C.rolling(200, min_periods=150).mean()
    hi52 = C.rolling(250, min_periods=150).max()
    last = df.groupby("ym")["date"].transform("max")
    me = df[df["date"] == last][["date", "ym", "code", "cap"]].copy()
    me["rank"] = me.groupby("ym")["cap"].rank(ascending=False, method="first")
    me = me[me["rank"] <= 100]
    ix = pd.MultiIndex.from_arrays([me["date"], me["code"]])
    me["close"] = C.stack().reindex(ix).to_numpy()
    me["ma200"] = ma200.stack().reindex(ix).to_numpy()
    me["hi52"] = hi52.stack().reindex(ix).to_numpy()
    return me, mr


def _fwd(mr: pd.DataFrame, m, codes: list[str], months: int) -> float:
    """m 다음 달부터 months 개월 동일 비중 수익 (없는 달은 0)."""
    i = mr.index.get_loc(m)
    seg = mr.iloc[i + 1:i + 1 + months][codes].fillna(0)
    if len(seg) < months:
        return np.nan
    return float(((1 + seg).prod() - 1).mean())


def _pct(x) -> str:
    return "-" if x is None or not np.isfinite(x) else f"{x * 100:+.0f}%"


def run(dataset: str = "kr", log=print) -> Path:
    t0 = time.time()
    raw = dart.load_raw()
    if raw.empty:
        raise SystemExit("DART 재무 자료가 없습니다. 연구 탭 'DART 재무 자료 받기'를 먼저 돌려 주세요.")
    log("재무 표 만드는 중…")
    qt, an = fundamentals(raw)
    log("주가 표 만드는 중…")
    panel = Panel.load(config.dataset_dir(dataset) / "prices.parquet")
    me, mr = monthly(panel)
    me = _asof(me, qt, ["ttm", "ttm_yoy"])
    me = _asof(me.drop(columns=["rcept"]), an.rename(columns={"rev": "arev"}), ["year", "arev", "yoy", "up3", "cagr3"])
    me["psr"] = me["cap"] / me["ttm"]
    me = me.sort_values(["code", "date"])
    # 자기 과거 5년(60개월, 최소 24개월) PSR 중간값 대비
    me["psr_med"] = me.groupby("code")["psr"].transform(lambda s: s.shift(1).rolling(60, min_periods=24).median())
    me["psr_rel"] = me["psr"] / me["psr_med"]
    names = dict(zip(panel.codes.astype(str), panel.names))
    first_fin = me.dropna(subset=["up3"])["date"].min()
    L = [f"매출 성장 회사 · {dt.date.today()} · 주가 ~{me['date'].max().date()} · 재무 {int(an['year'].min())}~{int(an['year'].max())} (DART)",
         f"대상: 매달 말 코스피 시총 상위 100 · 실적은 공시일부터 반영 · 3년 성장 정보가 생기는 {first_fin.date() if pd.notna(first_fin) else '-'}부터"]

    # 1부 -----------------------------------------------------------------
    k = me.copy()
    k["y"] = k["date"].dt.year
    rows = []
    for y in sorted(an["year"].dropna().unique()):
        y = int(y)
        start = k[(k["date"].dt.year == y - 1) & (k["date"].dt.month == 12)]  # 그해 초(전년 말) 상위 100
        if start.empty:
            continue
        a = an[an["year"] == y].set_index("stock_code")
        for code in start["code"]:
            if code not in a.index or not np.isfinite(a.loc[code, "yoy"]):
                continue
            ym0 = pd.Period(f"{y}-01", "M")
            same = mr.loc[ym0:pd.Period(f"{y}-12", "M"), code].fillna(0) if code in mr else pd.Series(dtype=float)
            after = mr.loc[pd.Period(f"{y + 1}-04", "M"):pd.Period(f"{y + 2}-03", "M"), code].fillna(0) if code in mr else pd.Series(dtype=float)
            rows.append({"y": y, "code": code, "g": a.loc[code, "yoy"],
                         "same": (1 + same).prod() - 1 if len(same) == 12 else np.nan,
                         "after": (1 + after).prod() - 1 if len(after) == 12 else np.nan})
    p1 = pd.DataFrame(rows)
    L += ["", "1부. 그해 매출 증가율 vs 주가 (그해 초 상위 100 회사, 회사 × 해)"]
    if not p1.empty:
        bins = [-np.inf, -0.10, 0, 0.10, 0.20, np.inf]
        labels = ["매출 -10% 이하", "-10~0%", "0~+10%", "+10~20%", "+20% 이상"]
        p1["b"] = pd.cut(p1["g"], bins, labels=labels)
        L.append(f"  {'':<16} {'회사×해':>7} {'같은 해 주가 중간값':>16} {'오른 비율':>8} {'공시 뒤 1년 중간값':>16} {'오른 비율':>8}")
        for b, g in p1.groupby("b", observed=True):
            L.append(f"  {b:<16} {len(g):>7,} {_pct(g['same'].median()):>16} {(g['same'] > 0).mean() * 100:>7.0f}% "
                     f"{_pct(g['after'].median()):>16} {(g['after'] > 0).mean() * 100:>7.0f}%")
        c1 = p1[["g", "same"]].corr(method="spearman").iloc[0, 1]
        c2 = p1[["g", "after"]].dropna().corr(method="spearman").iloc[0, 1]
        L.append(f"  순위 상관: 같은 해 {c1:+.2f} · 공시 뒤 1년 {c2:+.2f}  (1 = 완전히 같이 움직임, 0 = 관계 없음)")
        L.append("  공시 뒤 1년 = 사업보고서가 나오는 다음 해 4월부터 1년 (매출을 '알고 나서' 샀을 때)")

    # 2부 -----------------------------------------------------------------
    log("그룹별 이후 수익…")
    groups = {
        "상위 100 전체 (기준)": lambda x: x["up3"].notna(),
        "3년 연속 매출 증가": lambda x: x["up3"] == 3,
        "3년 연속 + 연 10% 이상": lambda x: (x["up3"] == 3) & (x["cagr3"] >= 0.10),
        "3년 성장률 상위 20%": lambda x: x["cagr3"] >= x["cagr3"].quantile(0.8),
        "최근 1년 매출 감소": lambda x: x["yoy"] < 0,
    }
    res = {g: {12: [], 24: [], 36: []} for g in groups}
    cnt = {g: [] for g in groups}
    for m, x in me[me["up3"].notna()].groupby("ym"):
        for g, f in groups.items():
            codes = [c for c in x[f(x)]["code"] if c in mr.columns]
            cnt[g].append(len(codes))
            for h in (12, 24, 36):
                res[g][h].append(_fwd(mr, m, codes, h) if codes else np.nan)
    L += ["", "2부. 그 달 말에 그룹 종목을 똑같이 나눠 샀다면 그 뒤 수익 (매달 시작한 것들의 평균, 괄호 = 상위 100 대비)",
          f"  {'':<22} {'평균 종목 수':>8} {'1년 뒤':>14} {'2년 뒤':>14} {'3년 뒤':>14} {'1년 뒤 기준보다 나은 달':>14}"]
    base = {h: np.array(res["상위 100 전체 (기준)"][h]) for h in (12, 24, 36)}
    for g in groups:
        cells = []
        for h in (12, 24, 36):
            a = np.array(res[g][h])
            ok = np.isfinite(a) & np.isfinite(base[h])
            cells.append(f"{_pct(np.nanmean(a[ok]) if ok.any() else np.nan)}({_pct(np.nanmean(a[ok] - base[h][ok]) if ok.any() else np.nan)})")
        a12 = np.array(res[g][12])
        ok = np.isfinite(a12) & np.isfinite(base[12])
        beat = (a12[ok] > base[12][ok]).mean() * 100 if ok.any() else np.nan
        L.append(f"  {g:<22} {np.mean(cnt[g]):>8.0f} {cells[0]:>14} {cells[1]:>14} {cells[2]:>14} {beat:>13.0f}%")

    # 3부 -----------------------------------------------------------------
    log("언제 살까…")
    steady = lambda x: x["up3"] == 3  # noqa: E731
    rules = [
        ("상위 100 전체 (기준)", lambda x: x["up3"].notna()),
        ("꾸준 성장 · 아무 때나", steady),
        ("꾸준 성장 · PSR 자기 5년 중간값보다 쌀 때", lambda x: steady(x) & (x["psr_rel"] < 1.0)),
        ("꾸준 성장 · PSR 중간값의 0.8배 아래", lambda x: steady(x) & (x["psr_rel"] < 0.8)),
        ("꾸준 성장 · 52주 고점보다 20% 이상 빠짐", lambda x: steady(x) & (x["close"] <= x["hi52"] * 0.8)),
        ("꾸준 성장 · 200일선 위", lambda x: steady(x) & (x["close"] > x["ma200"])),
        ("꾸준 성장 · 쌀 때 + 200일선 위", lambda x: steady(x) & (x["psr_rel"] < 1.0) & (x["close"] > x["ma200"])),
        ("3년 연속 + 10% 이상 · 쌀 때", lambda x: (x["up3"] == 3) & (x["cagr3"] >= 0.10) & (x["psr_rel"] < 1.0)),
    ]
    L += ["", "3부. 언제 살까: 매달 말 조건에 맞는 종목을 똑같이 나눠 한 달 보유 (없으면 현금) · 바뀐 비중 × 0.25%",
          f"  {'':<34} {'배수':>6} {'연':>7} {'최대 낙폭':>8} {'종목 수':>6}" + "".join(f"{n[:10]:>13}" for n, _, _ in PERIODS[2:-1])]
    me_f = me[me["up3"].notna()]
    months = sorted(me_f["ym"].unique())
    for label, f in rules:
        out, prev, ns = [], set(), []
        for m in months[:-1]:
            x = me_f[me_f["ym"] == m]
            pick = {c for c in x[f(x)]["code"] if c in mr.columns}
            nxt = mr.index[mr.index.get_loc(m) + 1] if mr.index.get_loc(m) + 1 < len(mr.index) else None
            if nxt is None:
                break
            ns.append(len(pick))
            if not pick:
                out.append((nxt, -COST if prev else 0.0))
                prev = set()
                continue
            changed = len(pick - prev) / len(pick) if prev else 1.0
            out.append((nxt, float(mr.loc[nxt, list(pick)].fillna(0).mean()) - COST * changed))
            prev = pick
        s = pd.Series({k_: v for k_, v in out})
        s.index = s.index.to_timestamp(how="end").normalize()
        eq = (1 + s).cumprod()
        yrs = max(len(s) / 12, 0.5)
        cells = []
        for _, a_, b_ in PERIODS[2:-1]:
            seg = s[(s.index >= a_) & (s.index <= pd.Timestamp(b_) + pd.offsets.MonthEnd(0))]
            cells.append(_pct((1 + seg).prod() - 1) if len(seg) else "-")
        L.append(f"  {label:<34} {eq.iloc[-1]:>5.1f}배 {(eq.iloc[-1] ** (1 / yrs) - 1) * 100:>+6.1f}% "
                 f"{(eq / eq.cummax() - 1).min() * 100:>7.1f}% {np.mean(ns):>6.0f}" + "".join(f"{c:>13}" for c in cells))
    kospi = index_load("KOSPI")
    if kospi is not None and len(months) > 1:
        ks = kospi[(kospi.index >= months[0].to_timestamp()) & (kospi.index <= months[-1].to_timestamp(how="end"))]
        if len(ks) > 1:
            L.append(f"  (같은 기간 코스피 지수 {ks.iloc[-1] / ks.iloc[0]:.1f}배)")

    # 4부 -----------------------------------------------------------------
    log("언제 팔까…")
    exits = [
        ("1년 들고 팔기", lambda r, held: held >= 12),
        ("2년 들고 팔기", lambda r, held: held >= 24),
        ("3년 들고 팔기", lambda r, held: held >= 36),
        ("매출 성장 꺾이면 (최근 4분기 매출 전년보다 감소)", lambda r, held: r["ttm_yoy"] < 0),
        ("비싸지면 (PSR 자기 중간값의 1.5배)", lambda r, held: r["psr_rel"] > 1.5),
        ("200일선 아래로 마감", lambda r, held: r["close"] < r["ma200"]),
        ("성장 꺾임 또는 비싸짐", lambda r, held: (r["ttm_yoy"] < 0) | (r["psr_rel"] > 1.5)),
        ("성장 꺾임 또는 비싸짐 또는 200일선", lambda r, held: (r["ttm_yoy"] < 0) | (r["psr_rel"] > 1.5) | (r["close"] < r["ma200"])),
    ]
    entry_ok = lambda r: (r["up3"] == 3) & (r["psr_rel"] < 1.0)  # noqa: E731
    bench = me_f.groupby("ym")["code"].apply(list)
    bm = pd.Series({m: float(mr.loc[mr.index[mr.index.get_loc(m) + 1], [c for c in bench[m] if c in mr.columns]].fillna(0).mean())
                    for m in months[:-1] if mr.index.get_loc(m) + 1 < len(mr.index)})
    by_code = {c: g.set_index("ym") for c, g in me.groupby("code")}
    mr_np = np.nan_to_num(mr.to_numpy(), nan=0.0)
    col = {c: j for j, c in enumerate(mr.columns)}
    month_set = set(months)
    L += ["", "4부. 언제 팔까: '3년 연속 매출 증가 + PSR 자기 중간값보다 쌀 때' 산 뒤 파는 규칙별 (월말 확인, 최대 5년, 0.25%)",
          f"  {'':<40} {'거래':>5} {'평균':>7} {'중간값':>7} {'수익 비율':>8} {'보유(월)':>8} {'상위100 대비':>10}"]
    for label, ex in exits:
        trades = []
        for code, g in by_code.items():
            if code not in mr.columns:
                continue
            ms = [m for m in g.index if m in month_set]
            cj = col[code]
            i = 0
            while i < len(ms) - 1:
                r = g.loc[ms[i]]
                if not bool(entry_ok(r)):
                    i += 1
                    continue
                m0 = ms[i]
                j0 = mr.index.get_loc(m0)
                held, val, bval = 0, 1.0, 1.0
                j = j0 + 1
                while j < len(mr.index):
                    val *= 1 + mr_np[j, cj]
                    bval *= 1 + bm.get(mr.index[j - 1], 0.0)
                    held += 1
                    mj = mr.index[j]
                    if held >= 60 or mj not in g.index or bool(ex(g.loc[mj], held)):
                        break
                    j += 1
                trades.append((val * (1 - COST) - 1, held, val / bval - 1))
                nxt = mr.index[min(j, len(mr.index) - 1)]
                i = next((k_ for k_, m in enumerate(ms) if m > nxt), len(ms))
        if not trades:
            L.append(f"  {label:<40} 거래 없음")
            continue
        t = np.array(trades)
        L.append(f"  {label:<40} {len(t):>5,} {_pct(t[:, 0].mean()):>7} {_pct(np.median(t[:, 0])):>7} "
                 f"{(t[:, 0] > 0).mean() * 100:>7.0f}% {t[:, 1].mean():>8.1f} {_pct(t[:, 2].mean()):>10}")
    L.append("  (한 번 판 종목은 판 다음 달부터 다시 조건을 보고 산다 · 지금 들고 있는 것은 오늘 값으로 계산)")

    # 지금 후보 -----------------------------------------------------------
    now = me[me["ym"] == me["ym"].max()]
    cand = now[(now["up3"] == 3) & (now["psr_rel"] < 1.0)].sort_values("psr_rel")
    L += ["", f"지금 ({now['date'].max().date()}) '3년 연속 매출 증가 + PSR 자기 중간값보다 쌈' 상위 100 회사 {len(cand)}곳:"]
    for r in cand.head(20).itertuples():
        L.append(f"  {names.get(r.code, r.code):<14} 3년 연 {r.cagr3 * 100:+.0f}% · 최근 4분기 {_pct(r.ttm_yoy)} · "
                 f"PSR {r.psr:.2f} (자기 중간값의 {r.psr_rel:.2f}배) · 200일선 {'위' if r.close > r.ma200 else '아래'}")
    L += ["", "읽는 법: 1부 같은 해 상관이 높고 공시 뒤 1년 상관이 낮으면 '매출은 주가에 이미 반영' → 사는 가격(3부)이 중요.",
          "2부 괄호가 플러스면 그 그룹이 상위 100 평균보다 나았던 것. 4부는 파는 규칙별 평균과 상위 100 대비 초과 수익.",
          "주의: 재무 자료가 2015년부터라 결과는 약 7~8년(2018~)치. 금융사는 매출 대신 영업수익이라 비교가 거칠 수 있음.",
          f"소요 {round((time.time() - t0) / 60, 1)}분"]
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"growth-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
