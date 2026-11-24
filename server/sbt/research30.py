"""코스피 시총 30위권 단타: 차트 패턴(망치형·장악형·돌파·삼각수렴 등)이 나온 다음 날 사면 남나.

  python -m sbt.data.cli top30-patterns

대상: 전달 말 코스피 시총 1~30위 보통주 중 삼성전자(005930)·SK하이닉스(000660) 제외 → 28종목 (달마다 바뀜).
일봉 기준. 패턴이 나온 날 종가로 확인 → 다음 날 시가에 산다. 비용: 살 때 0.015%, 팔 때 0.215% (수수료+세금).
'단타' = 하루~열흘 (분봉 과거 자료가 없어 장중 매매는 시험 못 함).

비교 기준 '아무 날': 같은 28종목을 아무 날이나 다음 날 시가에 샀을 때. 패턴이 진짜 효과가 있으면 이것보다 나아야 한다.
1부. 패턴별: 당일(시가→종가)·1·3·5·10일 보유 평균 · 이긴 비율 · '아무 날'보다 얼마나 나았나 · 과거(~2020)/검증(2021~)
2부. 목표·손절 규칙 (예: +3% 목표 · -2% 손절 · 최대 5일)
3부. 계좌 (5천만 · 1회 계좌 20% · 하루 최대 3종목) — 과거·검증 모두 '아무 날'보다 나은 규칙만
4부. 최근 20거래일에 나온 신호
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

TOP = 30
EXCLUDE = {"005930", "000660"}
CUT = np.datetime64("2021-01-01")
BUY, SELL = 0.00015, 0.00215
HMAX = 20
HOLDS = [(0, "당일"), (1, "1일"), (3, "3일"), (5, "5일"), (10, "10일")]
RULES = [("목표3 · 손절2 · 5일", 3, 2, 5), ("목표5 · 손절3 · 5일", 5, 3, 5), ("목표5 · 손절3 · 10일", 5, 3, 10),
         ("목표7 · 손절4 · 10일", 7, 4, 10), ("목표10 · 손절5 · 20일", 10, 5, 20)]


def universe(panel: Panel) -> np.ndarray:
    """행마다 '그날이 대상인가' (전달 말 코스피 시총 순위 ≤ TOP, 두 종목 제외)."""
    df = kospi_frame(panel, ("KOSPI",))
    ok = np.zeros(len(panel.dates), bool)
    m = (df["rank"] <= TOP) & ~df["code"].isin(EXCLUDE)
    ok[df.loc[m, "row"].to_numpy()] = True
    return ok


def _feat(panel: Panel, rows: np.ndarray) -> pd.DataFrame:
    p = panel
    c = p.cols
    df = pd.DataFrame({"row": rows, "code": p.codes[rows].astype(str), "pos": p.pos[rows],
                       "o": c["open"][rows], "h": c["high"][rows], "l": c["low"][rows], "c": c["close"][rows],
                       "v": c["volume"][rows] if "volume" in c else np.nan})
    g = df.groupby("code", sort=False)

    def sh(col: str, k: int = 1) -> pd.Series:
        return g[col].shift(k)

    def roll(s: pd.Series, n: int, fn: str) -> pd.Series:
        return s.groupby(df["code"], sort=False).transform(lambda x: getattr(x.rolling(n, min_periods=n), fn)())

    o, h, lo, cl = df["o"], df["h"], df["l"], df["c"]
    df["body"] = (cl - o).abs()
    df["rng"] = h - lo
    df["lower"] = np.minimum(o, cl) - lo
    df["upper"] = h - np.maximum(o, cl)
    for n in (20, 60):
        df[f"ma{n}"] = roll(cl, n, "mean")
    df["ma60_20"] = sh("ma60", 20)
    df["hh20p"] = roll(sh("h"), 20, "max")  # 어제까지 20일 최고가
    df["hh250p"] = roll(sh("h"), 250, "max")
    df["ll20"] = roll(lo, 20, "min")  # 오늘 포함 20일 최저가
    df["r10"] = cl / sh("c", 10) - 1
    df["vavg"] = roll(sh("v"), 20, "mean")
    df["c1"], df["o1"], df["h1"], df["l1"] = sh("c"), sh("o"), sh("h"), sh("l")
    df["c2"], df["c3"] = sh("c", 2), sh("c", 3)
    df["chg"] = cl / df["c1"] - 1
    # 볼린저 밴드 폭 (20일, 2σ)
    sd = roll(cl, 20, "std")
    df["bb_up"] = df["ma20"] + 2 * sd
    df["bbw"] = 4 * sd / df["ma20"]
    df["bbw_min"] = roll(df["bbw"], 120, "min")
    # 지난 20일(어제까지) 고가·저가 추세선 (최소제곱). 창 안 x = 0..n-1 (어제 = n-1, 오늘 = n)
    n = 20
    pos = df["pos"].astype(float)
    sx, sxx = n * (n - 1) / 2, (n - 1) * n * (2 * n - 1) / 6
    for col, key in (("h", "H"), ("l", "L")):
        y = sh(col)
        sy = roll(y, n, "sum")
        sxy = roll((pos - 1) * y, n, "sum") - (pos - n) * sy  # 창 첫 줄의 원래 x = pos - n
        b = (n * sxy - sx * sy) / (n * sxx - sx ** 2)
        a = (sy - b * sx) / n
        df[f"b{key}"] = b
        df[f"a{key}"] = a  # 창 첫날 위치
        df[f"y{key}"] = a + b * (n - 1)  # 어제 위치
        df[f"t{key}"] = a + b * n  # 오늘 위치
    return df


def patterns(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """패턴 이름 → 신호(그날 종가로 확인) 불리언."""
    o, h, lo, c, v = df["o"], df["h"], df["l"], df["c"], df["v"]
    body, rng, lower, upper = df["body"], df["rng"], df["lower"], df["upper"]
    up_trend = (c > df["ma60"]) & (df["ma20"] > df["ma60"]) & (df["ma60"] > df["ma60_20"])
    at_low = (lo <= df["ll20"]) & (c < df["ma20"])  # 오늘 20일 최저가를 찍었고 20일선 아래
    hammer = (rng / c >= 0.015) & (lower >= 2 * body) & (upper <= np.maximum(body, 0.1 * rng))
    vol_up = v >= 1.5 * df["vavg"]
    hammer_low = hammer & at_low
    hl_prev = hammer_low.groupby(df["code"], sort=False).shift(1).fillna(False).astype(bool)
    engulf = (df["c1"] < df["o1"]) & (c > o) & (o <= df["c1"]) & (c >= df["o1"]) & (body >= 1.2 * (df["o1"] - df["c1"]))
    engulf_low = engulf & ((df["l1"] <= df["ll20"] * 1.005) | (lo <= df["ll20"])) & (c < df["ma20"])
    breakout = up_trend & (c > df["hh20p"]) & (df["c1"] <= df["hh20p"])
    tri_shape = (df["bH"] < 0) & (df["bL"] > 0) & ((df["yH"] - df["yL"]) <= 0.6 * (df["aH"] - df["aL"])) & (df["aH"] > df["aL"])
    tri = tri_shape & (c > df["tH"]) & (df["c1"] <= df["yH"])
    flat_top = (df["bH"].abs() * 20 / c <= 0.02) & (df["bL"] * 20 / c >= 0.03) & ((df["yH"] - df["yL"]) <= 0.6 * (df["aH"] - df["aL"]))
    asc_tri = flat_top & (c > df["hh20p"]) & (df["c1"] <= df["hh20p"])
    squeeze = (df["bbw"].groupby(df["code"], sort=False).shift(1) <= 1.1 * df["bbw_min"]) & (c > df["bb_up"]) & (df["c1"] <= df["bb_up"])
    pull3 = up_trend & (c < df["c1"]) & (df["c1"] < df["c2"]) & (df["c2"] < df["c3"])
    drop4 = df["chg"] <= -0.04
    out = {
        "망치형 (바닥)": hammer_low,
        "망치형 (바닥) + 거래량 1.5배": hammer_low & vol_up,
        "망치형 (바닥) + 다음 날 확인": hl_prev & (c > df["h1"]),
        "상승장악형 (바닥)": engulf_low,
        "상승 추세 20일 고점 돌파": breakout,
        "상승 추세 돌파 + 거래량 1.5배": breakout & vol_up,
        "52주 신고가 돌파": (c > df["hh250p"]) & (df["c1"] <= df["hh250p"]),
        "삼각수렴 위로 돌파": tri,
        "상승삼각형 (평평한 천장) 돌파": asc_tri,
        "볼린저 수축 뒤 위로 돌파": squeeze,
        "상승 추세 중 3일 연속 하락 (눌림)": pull3,
        "하루 -4% 이상 급락": drop4,
    }
    return {k: np.asarray(vv.fillna(False), dtype=bool) for k, vv in out.items()}


class Book:
    """신호 다음 날 시가에 산 거래들의 0~HMAX 일 가격."""

    def __init__(self, panel: Panel, sig_rows: np.ndarray):
        p = panel
        sig_rows = sig_rows[sig_rows + 1 <= p.gend[sig_rows]]
        self.sig = sig_rows
        fr = sig_rows + 1
        self.fill = fr
        idx = fr[:, None] + np.arange(HMAX + 1)[None, :]
        self.valid = idx <= p.gend[fr][:, None]
        ii = np.where(self.valid, idx, 0)
        self.O, self.H, self.L, self.C = (np.where(self.valid, p.cols[k][ii], np.nan) for k in ("open", "high", "low", "close"))
        self.fp = self.O[:, 0]
        self.dates = p.dates
        self.code = p.codes[fr].astype(str)
        self.past = p.dates[fr] < CUT

    def hold(self, h: int) -> np.ndarray:
        with np.errstate(invalid="ignore", divide="ignore"):
            return ((self.C[:, h] * (1 - SELL)) / (self.fp * (1 + BUY)) - 1) * 100

    def rule(self, tgt: float, stop: float, hold: int) -> dict:
        n = len(self.fp)
        fp = self.fp
        price = np.full(n, np.nan)
        day = np.full(n, -1)
        alive = np.ones(n, bool)
        tp, sp = fp * (1 + tgt / 100), fp * (1 - stop / 100)
        for d in range(hold + 1):
            v = self.valid[:, d] & alive
            o, h, lo, c = self.O[:, d], self.H[:, d], self.L[:, d], self.C[:, d]
            with np.errstate(invalid="ignore"):
                if d > 0:  # 시가에 이미 넘어서 시작하면 시가에
                    gs = v & (o <= sp)
                    gt = v & ~gs & (o >= tp)
                    price[gs | gt], day[gs | gt] = o[gs | gt], d
                    alive &= ~(gs | gt)
                    v &= ~(gs | gt)
                hs = v & (lo <= sp)  # 같은 날 둘 다 닿으면 손절로 (불리하게)
                ht = v & ~hs & (h >= tp)
            price[hs], day[hs] = sp[hs], d
            price[ht], day[ht] = tp[ht], d
            alive &= ~(hs | ht)
            if d == hold:
                m = v & ~(hs | ht)
                price[m], day[m] = c[m], d
                alive &= ~m
        done = day >= 0
        ret = np.where(done, ((price * (1 - SELL)) / (fp * (1 + BUY)) - 1) * 100, np.nan)
        return {"ret": ret, "done": done, "day": day}

    def leg(self, ret: np.ndarray, day: np.ndarray) -> Leg:
        """끝난 거래(ret 이 숫자)만 담은 Leg — 계좌 시뮬레이션용."""
        k = np.flatnonzero(np.isfinite(ret))
        g = Leg.__new__(Leg)
        g.name, g.per, g.daily = "top30", 0, 0
        g.code = self.code[k]
        g.fill = self.dates[self.fill[k]]
        g.done = np.ones(len(k), bool)
        g.exit = self.dates[self.fill[k] + np.maximum(day[k], 0)]
        g.ret = ret[k]
        g.value = np.zeros(len(k))
        return g


def _m(x: np.ndarray) -> float:
    x = x[np.isfinite(x)]
    return float(x.mean()) if len(x) else float("nan")


def _t(x: np.ndarray, base: float) -> float:
    x = x[np.isfinite(x)]
    if len(x) < 10 or x.std() == 0:
        return float("nan")
    return float((x.mean() - base) / (x.std(ddof=1) / np.sqrt(len(x))))


def run(dataset: str = "kr", log=print) -> Path:
    t0 = time.time()
    log("데이터 읽는 중…")
    panel = Panel.load(config.dataset_dir(dataset) / "prices.parquet")
    if "mktcap" not in panel.cols:
        raise SystemExit("시가총액 자료가 없습니다.")
    uni = universe(panel)
    codes = np.unique(panel.codes[uni].astype(str))
    rows = np.flatnonzero(np.isin(panel.codes.astype(str), codes))
    log(f"대상 {len(codes)}종목 (한 번이라도 30위권) · 패턴 계산…")
    df = _feat(panel, rows)
    pats = patterns(df)
    in_uni = uni[rows]
    names = dict(zip(panel.codes.astype(str), panel.names))

    # 비교 기준: 대상인 날 전부
    base_bk = Book(panel, rows[in_uni])
    base = {h: base_bk.hold(h) for h, _ in HOLDS}
    base_m = {h: _m(base[h]) for h in base}
    base_pa = {h: _m(base[h][base_bk.past]) for h in base}
    base_va = {h: _m(base[h][~base_bk.past]) for h in base}
    base_rule = {lab: base_bk.rule(t, s, hd)["ret"] for lab, t, s, hd in RULES}
    yrs = max(len(np.unique(pd.DatetimeIndex(panel.dates[rows[in_uni]]).year)), 1)

    L = [f"코스피 시총 30위권 단타 · 차트 패턴 · {dt.date.today()} · 주가 ~{pd.Timestamp(panel.dates.max()).date()}",
         f"대상: 전달 말 코스피 시총 1~{TOP}위 (삼성전자·SK하이닉스 제외, 달마다 바뀜) · 거쳐 간 종목 {len(codes)}개 · "
         f"대상 일수 {int(in_uni.sum()):,}종목·일",
         "신호 날 종가로 확인 → 다음 날 시가 매수 · 비용 차감(살 때 0.015%, 팔 때 0.215%) · 과거 ~2020 / 검증 2021~", ""]
    hdr = "".join(f"{lab:>8}" for _, lab in HOLDS)
    L += ["비교 기준 '아무 날' (같은 종목을 아무 날이나 샀을 때)",
          f"  {'보유':<10}{hdr}",
          f"  {'평균':<10}" + "".join(f"{base_m[h]:>+7.2f}%" for h, _ in HOLDS),
          f"  {'이긴 비율':<10}" + "".join(f"{(base[h][np.isfinite(base[h])] > 0).mean() * 100:>7.0f}%" for h, _ in HOLDS),
          f"  {'과거':<10}" + "".join(f"{base_pa[h]:>+7.2f}%" for h, _ in HOLDS),
          f"  {'검증':<10}" + "".join(f"{base_va[h]:>+7.2f}%" for h, _ in HOLDS), ""]

    # 1부
    log("1부…")
    L.append("1부. 패턴별 보유 기간 성적 (평균은 비용 뺀 1건 수익 · '아무 날 대비' = 패턴 평균 − 아무 날 평균)")
    books: dict[str, Book] = {}
    summary = []
    for name, sig in pats.items():
        m = sig & in_uni
        if m.sum() < 15:
            L += [f"  ■ {name}: {int(m.sum())}건 — 너무 적어 건너뜀", ""]
            continue
        bk = Book(panel, rows[m])
        books[name] = bk
        r = {h: bk.hold(h) for h, _ in HOLDS}
        L += [f"  ■ {name} · {len(bk.fp):,}건 (한 해 약 {len(bk.fp) / yrs:.0f}건)",
              f"    {'보유':<10}{hdr}",
              f"    {'평균':<10}" + "".join(f"{_m(r[h]):>+7.2f}%" for h, _ in HOLDS),
              f"    {'이긴 비율':<10}" + "".join(f"{(r[h][np.isfinite(r[h])] > 0).mean() * 100:>7.0f}%" for h, _ in HOLDS),
              f"    {'아무 날 대비':<9}" + "".join(f"{_m(r[h]) - base_m[h]:>+7.2f}%" for h, _ in HOLDS),
              f"    {'t값':<10}" + "".join(f"{_t(r[h], base_m[h]):>8.1f}" for h, _ in HOLDS),
              f"    {'과거 대비':<9}" + "".join(f"{_m(r[h][bk.past]) - base_pa[h]:>+7.2f}%" for h, _ in HOLDS),
              f"    {'검증 대비':<9}" + "".join(f"{_m(r[h][~bk.past]) - base_va[h]:>+7.2f}%" for h, _ in HOLDS), ""]
        for h, lab in HOLDS:
            ex_pa = _m(r[h][bk.past]) - base_pa[h]
            ex_va = _m(r[h][~bk.past]) - base_va[h]
            summary.append((name, lab, _m(r[h]) - base_m[h], ex_pa, ex_va, len(bk.fp)))
    L += ["  (t값: 아무 날과의 차이가 우연인지. 대략 2 넘으면 의미 있고 1 아래면 우연과 구별 안 됨 · 같은 날 여러 종목이 같이 움직여 실제보다 크게 나오는 편)", ""]

    # 2부
    log("2부…")
    L += ["2부. 목표·손절 규칙 (1건 평균 · 이긴 비율 · 과거/검증 · 아무 날 같은 규칙 대비)",
          f"  {'아무 날':<24}" + " · ".join(f"{lab} {_m(base_rule[lab]):+.2f}%" for lab, *_ in RULES), ""]
    cand = []
    for name, bk in books.items():
        L.append(f"  ■ {name}")
        for lab, t, s, hd in RULES:
            x = bk.rule(t, s, hd)
            r = x["ret"]
            ok = np.isfinite(r)
            pa, va = _m(r[bk.past]), _m(r[~bk.past])
            bpa, bva = _m(base_rule[lab][base_bk.past]), _m(base_rule[lab][~base_bk.past])
            ex = _m(r) - _m(base_rule[lab])
            L.append(f"    {lab:<22}{_m(r):>+7.2f}%{(r[ok] > 0).mean() * 100:>6.0f}%  과거 {pa:>+5.2f} 검증 {va:>+5.2f}  대비 {ex:>+5.2f}%")
            if pa - bpa > 0 and va - bva > 0 and _m(r) > 0:
                cand.append((name, lab, t, s, hd, ex, x))
        L.append("")

    # 3부
    log("3부…")
    days = np.unique(panel.dates)
    L += ["3부. 계좌 5천만 · 1회 계좌 20%(최대 2천만) · 하루 최대 3종목 · 실현 손익 기준",
          "  (과거·검증 모두 아무 날보다 나은 규칙 중 대비가 큰 순 최대 6개 + 비교용 '아무 날')",
          f"  {'':<44}{'최종':>8}{'연':>8}{'2021~':>8}{'최대 낙폭':>9}{'산 것':>7}"]
    cand.sort(key=lambda z: -z[5])
    shown = 0
    for name, lab, t, s, hd, ex, x in cand:
        if shown >= 6:
            break
        bk = books[name]
        a = account(bk.leg(x["ret"], x["day"]), days, 50_000_000, None, 0.20, 3, 20_000_000)
        L.append(f"  {(name + ' · ' + lab)[:44]:<44}{a['final'] / 1e8:>6.2f}억{a['cagr']:>+7.1f}%{a['oos_cagr'] or 0:>+7.1f}%"
                 f"{a['mdd']:>8.1f}%{a['taken']:>7,}")
        shown += 1
    if not cand:
        L.append("  과거·검증 모두 아무 날보다 나은 목표·손절 규칙이 없습니다.")
    # 아무 날 (무작위 3종목/일) 비교: 너무 많으니 날마다 무작위 3개만
    rng = np.random.default_rng(0)
    pick = np.zeros(len(base_bk.fp), bool)
    fd = base_bk.dates[base_bk.fill]
    for d in np.unique(fd):
        ii = np.flatnonzero(fd == d)
        pick[rng.choice(ii, min(3, len(ii)), replace=False)] = True
    x = base_bk.rule(5, 3, 10)
    rr = np.where(pick, x["ret"], np.nan)
    a = account(base_bk.leg(rr, x["day"]), days, 50_000_000, None, 0.20, 3, 20_000_000)
    L.append(f"  {'(비교) 아무 날 무작위 3종목 · 목표5 · 손절3 · 10일':<44}{a['final'] / 1e8:>6.2f}억{a['cagr']:>+7.1f}%"
             f"{a['oos_cagr'] or 0:>+7.1f}%{a['mdd']:>8.1f}%{a['taken']:>7,}")

    # 요약: 보유 기간 기준 과거·검증 모두 플러스
    good = [z for z in summary if z[3] > 0.1 and z[4] > 0.1]
    good.sort(key=lambda z: -min(z[3], z[4]))
    L += ["", "요약: 과거·검증 둘 다 아무 날보다 0.1%p 이상 나았던 (패턴 · 보유) — 둘 중 작은 값 순"]
    L += [f"  {n_} · {lab} · 과거 {pa:+.2f}%p · 검증 {va:+.2f}%p · {k:,}건" for n_, lab, _, pa, va, k in good[:12]] or ["  없음"]

    # 4부
    last_days = np.unique(panel.dates[rows])[-20:]
    L += ["", "4부. 최근 20거래일 신호 (신호 날 · 종목 · 패턴)"]
    rec = []
    for name, sig in pats.items():
        m = sig & in_uni & np.isin(panel.dates[rows], last_days)
        for r in rows[m]:
            rec.append((panel.dates[r], names.get(str(panel.codes[r]), str(panel.codes[r])), name))
    rec.sort(key=lambda z: z[0], reverse=True)
    L += [f"  {pd.Timestamp(d).date()} {nm:<12} {pn}" for d, nm, pn in rec[:40]] or ["  없음"]
    L += ["", "읽는 법: '아무 날 대비'가 과거·검증 둘 다 플러스이고 t값이 2 근처 이상이어야 패턴 덕이라고 볼 만함.",
          "대형주는 하루 평균 움직임이 작아 1건 +0.3%p 차이도 비용(왕복 0.23%)을 생각하면 큰 편.",
          "결과가 약하면 '이 패턴으로 대형주 단타는 통계적 우위가 없다'가 결론 — 그것도 중요한 정보.",
          f"소요 {round((time.time() - t0) / 60, 1)}분"]
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"top30-patterns-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
