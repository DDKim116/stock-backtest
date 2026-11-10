"""전고점 세 번째 돌파: 1년 고점을 두 번 찍고(쌍봉) 내려왔다가 세 번째에 뚫고 올라가면 얼마나 먹을 수 있나.

  python -m sbt.data.cli third-touch

모양 (일봉, 코스피·코스닥 보통주, 우선주·스팩 제외):
  ① 첫 고점 P1: 그날 고가가 앞뒤 10거래일 중 가장 높고, 지난 1년(250일) 중에서도 가장 높음 (= 전고점)
  ② 두 번째 고점 P2: P1 뒤 20~250거래일 사이, 고가가 P1 의 ±3% 안 (다시 닿음). 사이에 P1 보다 높은 날 없음.
     P1 과 P2 사이에 저가가 저항선 R(= 둘 중 높은 값)보다 10% 이상 아래로 내려간 적이 있어야 함 (진짜 되돌림)
  ③ P2 뒤 다시 R 보다 7% 이상 내려왔다가, P2 뒤 120거래일 안에 종가가 R 을 처음 넘은 날 = 돌파일
  ④ 돌파일 거래대금 10억↑ · 종가 1,000원↑. 다음 날 시가에 산다. 비용: 살 때 0.015%, 팔 때 0.215%.
시가총액대: 돌파 전달 말 코스피+코스닥 합친 시총 순위 — 1~100위 · 101~300위 · 301~800위 · 801위~
1부. 산 뒤 얼마까지 갔나: 5·20·60·120거래일 뒤 수익, 120일 안에 +10·20·30·50% 에 닿은 비율 (-10% 먼저 닿기 전)
2부. 목표·손절·보유 기간 조합: 1건당 평균 (과거 ~2020 / 검증 2021~)
3부. 거래량 함께 터진 돌파(20일 평균 2배↑)만 따로
4부. 계좌 (5천만, 계좌 10% × 하루 5종목) — 2부에서 고른 규칙
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import config
from .data.panel import Panel
from .research7 import Leg
from .research10 import simulate as account
from .research14 import kospi_frame

K = 10  # 고점 판정: 앞뒤 10거래일
TOL = 0.03
PULL1, PULL2 = 0.10, 0.07
CUT = np.datetime64("2021-01-01")
BUY, SELL = 0.00015, 0.00215
GROUPS = [("1~100위", 0, 100), ("101~300위", 100, 300), ("301~800위", 300, 800), ("801위~", 800, 100_000)]
HMAX = 120


def _pivots(h: np.ndarray) -> np.ndarray:
    n = len(h)
    if n < 2 * K + 1:
        return np.zeros(0, dtype=int)
    win = np.lib.stride_tricks.sliding_window_view(h, 2 * K + 1)
    mx = win.max(axis=1)
    idx = np.arange(K, n - K)
    return idx[h[K:n - K] >= mx]


def find(panel: Panel) -> pd.DataFrame:
    """돌파 사건: row(돌파일 행), R(저항선), p1, p2(행), vol_ratio."""
    p = panel
    o, h, lo, c = (p.cols[k] for k in ("open", "high", "low", "close"))
    v = p.cols.get("volume")
    val = p.cols.get("value")
    ok_stock = np.isin(p.markets, ["KOSPI", "KOSDAQ"]) & ~p.is_preferred & ~p.is_spac
    starts = np.flatnonzero(p.pos == 0)
    out = []
    for s in starts:
        e = int(p.gend[s]) + 1
        if not ok_stock[s] or e - s < 300:
            continue
        hh, ll, cc = h[s:e], lo[s:e], c[s:e]
        hi250 = pd.Series(hh).rolling(250, min_periods=120).max().to_numpy()
        piv = _pivots(hh)
        piv = piv[np.isfinite(hi250[piv]) & (hh[piv] >= hi250[piv])]  # 1년 고점인 고점만 P1 후보
        allpiv = _pivots(hh)
        last_b = -1
        for i1 in piv:
            if i1 <= last_b:
                continue
            cand = allpiv[(allpiv >= i1 + 20) & (allpiv <= i1 + 250)]
            for i2 in cand:
                h1, h2 = hh[i1], hh[i2]
                if abs(h2 / h1 - 1) > TOL:
                    continue
                R = max(h1, h2)
                if hh[i1 + 1:i2].max(initial=0) > R:
                    break  # 사이에 더 높은 날 → 이 P1 은 끝
                if ll[i1:i2].min() > R * (1 - PULL1):
                    continue
                # P2 확인(앞뒤 10일) 뒤부터 돌파 찾기
                j0, j1 = i2 + 1, min(i2 + HMAX, e - s - 2)
                if j1 <= i2 + K:
                    break
                seg_c = cc[j0:j1 + 1]
                above = np.flatnonzero(seg_c > R)
                above = above[above + j0 > i2 + K]
                if not len(above):
                    break
                b = int(above[0] + j0)
                if ll[i2:b].min() > R * (1 - PULL2) or cc[i2 + 1:b].max(initial=0) > R:
                    break
                row = s + b
                if c[row] < 1000 or (val is not None and val[row] < 1e9):
                    break
                vr = float(v[row] / np.nanmean(v[max(row - 20, s):row])) if v is not None and row - s > 5 else np.nan
                out.append({"row": row, "R": R, "p1": s + i1, "p2": s + i2, "vol_ratio": vr})
                last_b = b
                break
    return pd.DataFrame(out)


def _rank(panel: Panel) -> np.ndarray:
    df = kospi_frame(panel, ("KOSPI", "KOSDAQ"))
    rk = np.full(len(panel.dates), np.nan)
    rk[df["row"].to_numpy()] = df["rank"].to_numpy()
    return rk


class Book:
    """돌파 다음 날 시가에 산 거래들의 0~HMAX 일 가격."""

    def __init__(self, panel: Panel, ev: pd.DataFrame):
        p = panel
        fr = ev["row"].to_numpy() + 1
        ok = fr <= p.gend[ev["row"].to_numpy()]
        self.ev = ev[ok].reset_index(drop=True)
        fr = fr[ok]
        self.fill = fr
        idx = fr[:, None] + np.arange(HMAX + 1)[None, :]
        self.valid = idx <= p.gend[fr][:, None]
        ii = np.where(self.valid, idx, 0)
        self.O, self.H, self.L, self.C = (np.where(self.valid, p.cols[k][ii], np.nan) for k in ("open", "high", "low", "close"))
        self.fp = self.O[:, 0]
        self.dates = p.dates
        self.code = p.codes[fr].astype(str)
        v = p.cols.get("value")
        self.value = v[fr - 1] if v is not None else np.zeros(len(fr))

    def run(self, tgt: float | None, stop: float | None, hold: int, r_stop: float | None = None) -> dict:
        """tgt·stop: % (None=없음), r_stop: 저항선 R 아래 x% 로 마감하면 다음 날 시가 매도."""
        n = len(self.fp)
        fp = self.fp
        R = self.ev["R"].to_numpy()
        price = np.full(n, np.nan)
        day = np.full(n, -1)
        kind = np.full(n, "", dtype=object)
        alive = np.ones(n, bool)
        sell_open = np.zeros(n, bool)  # 전날 종가로 R 정리 신호
        tp = fp * (1 + tgt / 100) if tgt else np.full(n, np.inf)
        sp = fp * (1 - stop / 100) if stop else np.full(n, -np.inf)
        for d in range(hold + 1):
            v = self.valid[:, d] & alive
            o, h, lo, c = self.O[:, d], self.H[:, d], self.L[:, d], self.C[:, d]
            with np.errstate(invalid="ignore"):
                if d > 0:
                    m = v & sell_open
                    price[m], day[m], kind[m] = o[m], d, "R이탈"
                    alive &= ~m
                    v &= ~m
                    gs = v & (o <= sp)
                    gt = v & ~gs & (o >= tp)
                    for mm, k, px in ((gs, "손절", o), (gt, "목표", o)):
                        price[mm], day[mm], kind[mm] = px[mm], d, k
                        alive &= ~mm
                    v &= ~(gs | gt)
                hs = v & (lo <= sp)
                ht = v & ~hs & (h >= tp)
            price[hs], day[hs], kind[hs] = sp[hs], d, "손절"
            price[ht], day[ht], kind[ht] = tp[ht], d, "목표"
            alive &= ~(hs | ht)
            if r_stop is not None:
                with np.errstate(invalid="ignore"):
                    sell_open = alive & self.valid[:, d] & (c < R * (1 - r_stop / 100))
            if d == hold:
                m = v & ~(hs | ht)
                price[m], day[m], kind[m] = c[m], d, "만료"
                alive &= ~m
        done = day >= 0
        ret = np.where(done, ((price * (1 - SELL)) / (fp * (1 + BUY)) - 1) * 100, 0.0)
        g = Leg.__new__(Leg)
        g.name, g.per, g.daily = "third", 0, 0
        g.code = self.code
        g.fill = self.dates[self.fill]
        g.done = done
        er = np.where(done, self.fill + np.maximum(day, 0), 0)
        g.exit = np.where(done, self.dates[er], np.datetime64("NaT"))
        g.ret = ret
        g.value = self.value
        return {"ret": ret, "done": done, "kind": kind, "leg": g}


def _q(x: np.ndarray, q: float) -> float:
    return float(np.nanpercentile(x, q)) if len(x) else float("nan")


def run(dataset: str = "kr", log=print) -> Path:
    t0 = time.time()
    log("데이터 읽는 중…")
    panel = Panel.load(config.dataset_dir(dataset) / "prices.parquet")
    log("모양 찾는 중 (종목마다)…")
    ev = find(panel)
    if ev.empty:
        raise SystemExit("모양에 맞는 돌파가 없습니다.")
    rk = _rank(panel)
    ev["rank"] = rk[ev["row"].to_numpy()]
    ev["grp"] = None
    for name, a, b in GROUPS:
        ev.loc[(ev["rank"] > a) & (ev["rank"] <= b), "grp"] = name
    ev = ev[ev["grp"].notna()].reset_index(drop=True)
    bk = Book(panel, ev)
    ev = bk.ev
    past = bk.dates[bk.fill] < CUT
    grp = ev["grp"].to_numpy()
    fp = bk.fp
    n_all = len(ev)
    yrs = pd.DatetimeIndex(bk.dates[bk.fill]).year
    L = [f"전고점 세 번째 돌파 · {dt.date.today()} · 주가 ~{pd.Timestamp(panel.dates.max()).date()} · {n_all:,}건 (한 해 약 {n_all / max(len(set(yrs)), 1):.0f}건)",
         "1년 고점 두 번(±3%, 사이 -10% 되돌림) → 다시 -7% 내려왔다가 종가로 돌파 → 다음 날 시가 매수 · 비용 차감 · 배당 제외", ""]
    groups = [("전체", np.ones(len(ev), bool))] + [(g, grp == g) for g, _, _ in GROUPS if (grp == g).sum() >= 10]

    # 1부
    L += ["1부. 산 뒤 얼마까지 갔나",
          f"  {'':<10}{'건수':>6}" + "".join(f"{f'{h}일 뒤':>15}" for h in (5, 20, 60, 120)) + f"{'120일 최고 중간값':>16}"]
    for gname, m in groups:
        cells = []
        for h in (5, 20, 60, 120):
            c = bk.C[m, h]
            r = (c / fp[m] - 1) * 100
            r = r[np.isfinite(r)]
            cells.append(f"{np.mean(r):+.1f}%({(r > 0).mean() * 100:.0f}%)" if len(r) else "-")
        mfe = (np.nanmax(bk.H[m], axis=1) / fp[m] - 1) * 100
        L.append(f"  {gname:<10}{int(m.sum()):>6,}" + "".join(f"{x:>15}" for x in cells) + f"{_q(mfe, 50):>+15.0f}%")
    L += ["  (괄호 = 플러스 비율)", "", "  120일 안에 먼저 -10% 에 닿기 전에 닿은 비율"]
    L.append(f"  {'':<10}" + "".join(f"{f'+{t}%':>8}" for t in (10, 20, 30, 50)))
    for gname, m in groups:
        cells = []
        for t in (10, 20, 30, 50):
            H, Lw = bk.H[m], bk.L[m]
            f = fp[m][:, None]
            up = np.where(np.isfinite(H), H >= f * (1 + t / 100), False)
            dn = np.where(np.isfinite(Lw), Lw <= f * 0.9, False)
            du = np.where(up.any(axis=1), up.argmax(axis=1), 10_000)
            dd = np.where(dn.any(axis=1), dn.argmax(axis=1), 10_000)
            hit = du < dd  # 같은 날 둘 다면 손절로 (불리하게)
            cells.append(f"{hit.mean() * 100:.0f}%")
        L.append(f"  {gname:<10}" + "".join(f"{x:>8}" for x in cells))

    # 2부
    log("목표·손절 조합…")
    rules = [("목표10 · 손절7 · 20일", 10, 7, 20, None), ("목표15 · 손절7 · 40일", 15, 7, 40, None),
             ("목표20 · 손절10 · 60일", 20, 10, 60, None), ("목표30 · 손절10 · 120일", 30, 10, 120, None),
             ("목표50 · 손절10 · 120일", 50, 10, 120, None), ("목표 없음 · 손절10 · 120일", None, 10, 120, None),
             ("목표20 · R 아래 5% 마감시 정리 · 60일", 20, None, 60, 5),
             ("목표30 · R 아래 5% 정리 · 120일", 30, None, 120, 5),
             ("목표 없음 · R 아래 5% 정리 · 120일", None, None, 120, 5)]
    L += ["", "2부. 목표·손절·보유 조합 (시총대마다 · 1건당 평균 · 이긴 비율 · 과거 ~2020 / 검증 2021~)"]
    best: dict[str, tuple] = {}
    res = {lab: bk.run(t, s_, hd, rs) for lab, t, s_, hd, rs in rules}
    for gname, m in groups:
        L += [f"  ■ {gname} ({int(m.sum()):,}건)", f"    {'':<36}{'1건 평균':>9}{'이긴 비율':>9}{'과거':>8}{'검증':>8}"]
        for label, x in res.items():
            mm = m & x["done"]
            r = x["ret"][mm]
            if not len(r):
                continue
            pa, va = x["ret"][mm & past], x["ret"][mm & ~past]
            L.append(f"    {label:<36}{r.mean():>+8.2f}%{(r > 0).mean() * 100:>8.0f}%"
                     f"{(pa.mean() if len(pa) else float('nan')):>+7.1f}%{(va.mean() if len(va) else float('nan')):>+7.1f}%")
            if gname not in best or r.mean() > best[gname][1]:
                best[gname] = (label, r.mean())
    L.append("  시총대별 1건당 가장 좋았던 규칙: " + " · ".join(f"{g} {b[0]} ({b[1]:+.1f}%)" for g, b in best.items()))

    # 3부
    vm = ev["vol_ratio"].to_numpy() >= 2
    x = res["목표20 · 손절10 · 60일"]
    L += ["", "3부. 돌파일 거래량이 20일 평균 2배↑ 인 것만 (목표20 · 손절10 · 60일)"]
    for gname, m in groups:
        a, b = x["ret"][m & vm & x["done"]], x["ret"][m & ~vm & x["done"]]
        if len(a) + len(b):
            L.append(f"  {gname:<10} 거래량 터짐 {len(a):>5,}건 {a.mean() if len(a) else 0:+.1f}% · 아님 {len(b):>5,}건 {b.mean() if len(b) else 0:+.1f}%")

    # 4부
    log("계좌…")
    days = np.unique(panel.dates)
    L += ["", "4부. 계좌 5천만 · 계좌 10% × 하루 5종목 (1회 최대 2천만) · 실현 손익 기준",
          f"  {'':<38}{'최종':>8}{'연':>8}{'2021~':>8}{'최대 낙폭':>9}{'노는 돈':>7}{'산 것':>7}"]
    for label in dict.fromkeys(("목표20 · 손절10 · 60일", "목표30 · 손절10 · 120일", "목표 없음 · R 아래 5% 정리 · 120일", best["전체"][0])):
        a = account(res[label]["leg"], days, 50_000_000, None, 0.10, 5, 20_000_000)
        L.append(f"  {label:<38}{a['final'] / 1e8:>6.2f}억{a['cagr']:>+7.1f}%{a['oos_cagr'] or 0:>+7.1f}%{a['mdd']:>8.1f}%"
                 f"{a['idle']:>6.0f}%{a['taken']:>7,}")
    # 해마다 건수
    cnt = pd.Series(yrs).value_counts().sort_index()
    L += ["", "  해마다 건수: " + " · ".join(f"{y} {n}" for y, n in cnt.items())]
    names = dict(zip(panel.codes.astype(str), panel.names))
    rec = ev.assign(d=bk.dates[bk.fill]).sort_values("d", ascending=False).head(15)
    L += ["", "최근 돌파 (산 날 · 종목 · 시총대 · 저항선):"]
    for r in rec.itertuples():
        L.append(f"  {pd.Timestamp(r.d).date()} {names.get(panel.codes[r.row], panel.codes[r.row]):<14} {r.grp:<9} R {r.R:,.0f}")
    L += ["", "읽는 법: 1부는 '보통 얼마나 가나', 2부는 '어떻게 팔면 가장 많이 남나'. 과거와 검증이 둘 다 플러스여야 믿을 만함.",
          "v2b(1건 약 +2.5%, 계좌 연 17%대)와 비교해 보고, 같이 쓰려면 따로 계좌가 원칙.",
          f"소요 {round((time.time() - t0) / 60, 1)}분"]
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"third-touch-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
