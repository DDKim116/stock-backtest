"""공시 따라가기: 자사주 매입 결정 · 임원·주요주주 장내 매수 공시 뒤에 사면 주가가 더 갈까.

  python -m sbt.data.cli disclosure-test     (먼저 dart-events)

대상: 코스피 + 코스닥 보통주. 공시 '다음 거래일 종가'에 산 것으로 친다 (장 끝나고 나온 공시도 있으니까).
그룹: 공시 전달 말 시총 순위(코스피+코스닥 합쳐) 상위 100 / 101~500위 / 501위~.
초과 = 같은 그룹 동일 비중 평균 대비. 같은 회사 같은 신호가 30일 안에 또 나오면 첫 번째만.
신호
- 자사주: 직접 매입 결정 / 신탁 계약 (신탁은 '살 수 있다'는 약속이라 약함)
- 임원·주요주주 (지분 보고서의 증감 수량이 + 인 것):
  아무나 매수 · 등기임원 매수 · 주요주주 매수 · 지분 0.1%p 이상 매수 · 30일 안에 2명 이상 매수(무리 지어 삼)
  (비교) 매도
주의: 지분 보고서의 + 에는 스톡옵션 행사·상속·증여도 섞여 있다 (장내 매수만 고르지 못함).
2부. 신호마다 60거래일 들고 가는 계좌 (그날 들고 있는 것 똑같이 나눔, 없으면 현금, 사고팔 때 0.25%).
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import config
from .data import dart_events
from .data.indices import load as index_load
from .data.panel import Panel
from .research14 import PERIODS, kospi_frame

HORIZONS = (20, 60, 120, 250)
HOLD = 60
COST = 0.0025
GROUPS = {"상위 100": (0, 100), "101~500위": (100, 500), "501위~": (500, 10_000)}


class Frame:
    """종목 × 날짜 수익률, 누적 로그, 그룹별 동일 비중 기준."""

    def __init__(self, panel: Panel, markets: tuple[str, ...] = ("KOSPI", "KOSDAQ")):
        df = kospi_frame(panel, markets)
        self.R = df.pivot_table(index="date", columns="code", values="ret")
        self.RK = df.pivot_table(index="date", columns="code", values="rank").reindex(
            index=self.R.index, columns=self.R.columns).to_numpy()
        self.dates = self.R.index.to_numpy(dtype="datetime64[ns]")
        self.Rv = self.R.to_numpy()
        self.cum = np.vstack([np.zeros(self.R.shape[1]), np.cumsum(np.log1p(np.nan_to_num(self.Rv)), axis=0)])
        self.col = {c: j for j, c in enumerate(self.R.columns)}
        self.bench = {}
        for g, (lo, hi) in GROUPS.items():
            m = (self.RK > lo) & (self.RK <= hi)
            b = np.where(m, self.Rv, np.nan)
            cnt = np.isfinite(b).sum(axis=1)
            br = np.where(cnt > 0, np.nansum(b, axis=1) / np.maximum(cnt, 1), 0.0)
            self.bench[g] = (br, np.r_[0.0, np.cumsum(np.log1p(br))])

    def place(self, ev: pd.DataFrame) -> pd.DataFrame:
        """ev(code, date) → t0(공시 다음 거래일 인덱스), j, grp 붙여서 돌려준다."""
        e = ev[ev["code"].isin(self.col)].copy()
        if e.empty:
            return e.assign(t0=[], j=[], grp=[])
        e["t0"] = np.searchsorted(self.dates, pd.to_datetime(e["date"]).to_numpy(dtype="datetime64[ns]"), side="right")
        e = e[e["t0"] < len(self.dates) - 1]
        e["j"] = e["code"].map(self.col).astype(int)
        rk = self.RK[e["t0"].to_numpy(), e["j"].to_numpy()]
        grp = np.full(len(e), None, dtype=object)
        for g, (lo, hi) in GROUPS.items():
            grp[(rk > lo) & (rk <= hi)] = g
        e["grp"] = grp
        return e[e["grp"].notna()]

    def excess(self, e: pd.DataFrame, h: int) -> np.ndarray:
        t, j = e["t0"].to_numpy(), e["j"].to_numpy()
        ok = t + h < len(self.dates)
        out = np.full(len(e), np.nan)
        if not ok.any():
            return out
        s = np.exp(self.cum[t[ok] + h + 1, j[ok]] - self.cum[t[ok] + 1, j[ok]]) - 1
        b = np.empty(ok.sum())
        gs = e["grp"].to_numpy()[ok]
        for g in GROUPS:
            m = gs == g
            bc = self.bench[g][1]
            b[m] = np.exp(bc[t[ok][m] + h + 1] - bc[t[ok][m] + 1]) - 1
        out[ok] = s - b
        return out

    def portfolio(self, e: pd.DataFrame, hold: int = HOLD) -> tuple[np.ndarray, float, float]:
        act = np.zeros_like(self.Rv)
        buys = np.zeros(len(self.dates))
        for t, j in zip(e["t0"].to_numpy(), e["j"].to_numpy()):
            act[t + 1:t + 1 + hold, j] = 1.0
            buys[t] += 1
        n = act.sum(axis=1)
        r = np.where(n > 0, np.nansum(act * np.nan_to_num(self.Rv), axis=1) / np.maximum(n, 1), 0.0)
        prev_n = np.r_[0.0, n[:-1]]
        r = r - COST * np.where(prev_n > 0, buys / np.maximum(prev_n + buys, 1), np.where(buys > 0, 1.0, 0.0))
        return r, float(n[n > 0].mean()) if (n > 0).any() else 0.0, float((n > 0).mean())


def dedupe(e: pd.DataFrame, days: int = 30) -> pd.DataFrame:
    """같은 회사에서 앞 신호 뒤 days 일 안에 또 나온 것은 뺀다."""
    if e.empty:
        return e
    e = e.sort_values(["code", "date"])
    keep, last = [], {}
    for code, d in zip(e["code"], e["date"]):
        p = last.get(code)
        ok = p is None or (d - p).days > days
        keep.append(ok)
        if ok:
            last[code] = d
    return e[np.array(keep)]


def clusters(buys: pd.DataFrame, days: int = 30, k: int = 2) -> pd.DataFrame:
    """days 일 안에 서로 다른 보고자 k명 이상이 산 날 (k번째 사람이 공시한 날)."""
    out = []
    for code, g in buys.sort_values("date").groupby("code"):
        d = g["date"].to_numpy()
        w = g["who"].astype(str).to_numpy()
        for i in range(len(g)):
            lo = np.searchsorted(d, d[i] - np.timedelta64(days, "D"), side="left")
            if len(set(w[lo:i + 1])) >= k:
                out.append({"code": code, "date": pd.Timestamp(d[i])})
    return pd.DataFrame(out, columns=["code", "date"])


def signals() -> list[tuple[str, pd.DataFrame]]:
    bb = dart_events.load("buyback")
    ins = dart_events.load("insider")
    out: list[tuple[str, pd.DataFrame]] = []
    if len(bb):
        out += [("자사주 직접 매입 결정", bb[bb["kind"] == "직접"]), ("자사주 신탁 계약", bb[bb["kind"] == "신탁"])]
    if len(ins):
        ins = ins.copy()
        ins["chg"] = pd.to_numeric(ins["chg"], errors="coerce")
        ins["chg_rate"] = pd.to_numeric(ins["chg_rate"], errors="coerce")
        buy = ins[ins["chg"] > 0]
        reg = buy["reg"].astype(str)
        out += [("임원·주주 아무나 매수", buy),
                ("등기임원 매수", buy[reg.str.contains("등기") & ~reg.str.contains("비등기")]),
                ("주요주주 매수", buy[buy["major"].astype(str).str.contains("주요")]),
                ("지분 0.1%p 이상 매수", buy[buy["chg_rate"] >= 0.1]),
                ("30일 안에 2명 이상 매수", clusters(buy)),
                ("(비교) 임원·주주 매도", ins[ins["chg"] < 0])]
    return [(n, dedupe(e[["code", "date"]].drop_duplicates())) for n, e in out]


def run(dataset: str = "kr", log=print) -> Path:
    t0 = time.time()
    sig = signals()
    if not sig:
        raise SystemExit("공시 사건 자료가 없습니다. 연구 탭 'DART 공시 사건 받기'를 먼저 돌려 주세요.")
    log("주가 표 만드는 중…")
    panel = Panel.load(config.dataset_dir(dataset) / "prices.parquet")
    fr = Frame(panel)
    placed = [(n, fr.place(e)) for n, e in sig]
    first = min((pd.Timestamp(e["date"].min()) for _, e in placed if len(e)), default=fr.R.index[0])
    L = [f"공시 따라가기 · {dt.date.today()} · 주가 ~{fr.R.index[-1].date()} · 공시 {first.date()}~ · 코스피+코스닥",
         "공시 다음 거래일 종가에 산 것으로 계산 · 초과 = 같은 시총 그룹 동일 비중 평균 대비 · 배당 제외", "",
         "1부. 신호 뒤 초과 수익 (평균, 괄호 = 초과가 플러스인 비율)"]
    for g in [None, *GROUPS]:
        L += ["", f"  ■ {g or '전체'}", f"  {'':<26} {'건수':>6}" + "".join(f"{f'{h}일 뒤':>14}" for h in HORIZONS)]
        for n, e in placed:
            x = e if g is None else e[e["grp"] == g]
            if x.empty:
                L.append(f"  {n:<26} {0:>6}")
                continue
            cells = []
            for h in HORIZONS:
                v = fr.excess(x, h)
                v = v[np.isfinite(v)]
                cells.append(f"{v.mean() * 100:+.1f}%({(v > 0).mean() * 100:.0f}%)" if len(v) else "-")
            L.append(f"  {n:<26} {len(x):>6,}" + "".join(f"{c:>14}" for c in cells))

    # 해마다: 60일 초과
    L += ["", "  해마다 60일 초과 (건수)"]
    for n, e in placed:
        if e.empty:
            continue
        v = fr.excess(e, 60)
        yrs = pd.to_datetime(e["date"]).dt.year.to_numpy()
        cells = []
        for y in sorted(set(yrs)):
            m = (yrs == y) & np.isfinite(v)
            if m.any():
                cells.append(f"{y} {v[m].mean() * 100:+.0f}%({m.sum()})")
        L.append(f"  {n}")
        for k in range(0, len(cells), 6):
            L.append("    " + "  ".join(cells[k:k + 6]))

    # 2부: 계좌
    log("계좌 굴리기…")
    start = int(min((e["t0"].min() for _, e in placed if len(e)), default=0))
    L += ["", f"2부. 신호마다 {HOLD}거래일 들고 가는 계좌 (같이 들고 있는 것 똑같이 나눔, 없으면 현금, 사고팔 때 {COST * 100:.2f}%)",
          f"  {'':<30} {'배수':>6} {'연':>7} {'최대 낙폭':>8} {'평균 종목':>7} {'투자':>5}" + "".join(
              f"{nm[:10]:>13}" for nm, _, _ in PERIODS[2:-1])]

    def line(label: str, r: np.ndarray, n_avg: float | None, inv: float | None) -> str:
        s = pd.Series(r[start:], index=fr.R.index[start:])
        eq = (1 + s).cumprod()
        yrs = max((s.index[-1] - s.index[0]).days / 365.25, 0.5)
        cells = []
        for _, a_, b_ in PERIODS[2:-1]:
            seg = s[(s.index >= a_) & (s.index <= b_)]
            cells.append(f"{((1 + seg).prod() - 1) * 100:+.0f}%" if len(seg) else "-")
        extra = f"{n_avg:>7.0f} {inv * 100:>4.0f}%" if n_avg is not None else f"{'':>7} {'':>5}"
        return (f"  {label:<30} {eq.iloc[-1]:>5.1f}배 {(eq.iloc[-1] ** (1 / yrs) - 1) * 100:>+6.1f}% "
                f"{(eq / eq.cummax() - 1).min() * 100:>7.1f}% {extra}" + "".join(f"{c:>13}" for c in cells))

    for g in ("101~500위", "501위~"):
        L.append(line(f"{g} 평균 (기준)", fr.bench[g][0], None, None))
        for n, e in placed:
            x = e[e["grp"] == g]
            if len(x) < 20 or n.startswith("(비교)"):
                continue
            r, na, inv = fr.portfolio(x)
            L.append(line(f"  {n}", r, na, inv))
        L.append("")
    k = index_load("KOSPI")
    if k is not None:
        ks = k[(k.index >= fr.R.index[start]) & (k.index <= fr.R.index[-1])]
        if len(ks) > 1:
            L.append(f"  (같은 기간 코스피 지수 {ks.iloc[-1] / ks.iloc[0]:.1f}배)")

    names = dict(zip(panel.codes.astype(str), panel.names))
    L += ["", "최근 30일 신호 (자사주 직접 매입 · 2명 이상 매수):"]
    cut = fr.R.index[-1] - pd.Timedelta(days=30)
    for n, e in placed:
        if n not in ("자사주 직접 매입 결정", "30일 안에 2명 이상 매수"):
            continue
        for r in e[pd.to_datetime(e["date"]) >= cut].sort_values("date", ascending=False).head(15).itertuples():
            L.append(f"  {pd.Timestamp(r.date).date()} {names.get(r.code, r.code):<14} {r.grp:<9} {n}")
    L += ["", "읽는 법: 60·120일 초과가 +2% 이상이고 괄호가 55% 이상, 해마다 고르게 플러스면 쓸 만한 신호.",
          "2부에서 기준(그룹 평균)보다 연 수익이 높고 낙폭이 비슷하거나 작으면 '따로 계좌' 후보.",
          "매도는 비교용: 매도 뒤가 기준보다 나쁘면 '임원이 판 종목은 v2b 에서 피하기' 같은 거름망을 생각해 볼 수 있음.",
          f"소요 {round((time.time() - t0) / 60, 1)}분"]
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"disclosure-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
