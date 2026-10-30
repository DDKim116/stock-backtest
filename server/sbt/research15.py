"""v2b 대형주 빼기 · 대형주 신고가 더하기 · 주도주 계좌.

  python -m sbt.data.cli leader-test

1부. v2b 계좌(5천만, 계좌 10% × 하루 10종목, 1회 최대 2천만 = 지금 설정)에서 시총 상위 종목을 빼면?
     순위는 매달 말 시가총액 기준을 다음 달에 쓴다 (box-test 와 같음).
2부. v2b 에 '추세장일 때 코스피 상위 100 의 52주 신고가'를 더해 같은 계좌로 굴리면? (고정 500만, 돈 같이 쓰기)
3부. 주도주 계좌 (따로 굴리는 계좌): 매달 말 코스피 상위 100 중 최근 6·12개월 가장 많이 오른 종목 5·10개를
     똑같이 나눠 한 달 들고, 다음 달 말 다시 고른다. 코스피가 200일선 아래면 현금. 비용(바뀐 만큼 0.25%) 차감, 배당 제외.
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import breadth, config
from .data.indices import attach as indices_attach
from .data.indices import load as index_load
from .data.panel import Panel
from .engine import dsl, event
from .engine.run import Runner
from .research import _family_spec
from .research2 import _signals, _sim
from .research7 import V2B, Leg, portfolio
from .research10 import simulate as size_sim
from .research14 import BOX_DAY, PERIODS, STRATS, kospi_frame

CAPITAL = 50_000_000
COST = 0.0025  # 바꾼 비중만큼 (매도세·수수료 대략)
NEW_HIGH = STRATS[5][2]  # box-test 의 52주 신고가


def _rank_rows(panel: Panel, markets: tuple[str, ...]) -> np.ndarray:
    """행마다 '전달 말 시총 순위' (markets 안에서). 모르면 NaN."""
    df = kospi_frame(panel, markets)
    r = np.full(len(panel), np.nan)
    r[df["row"].to_numpy()] = df["rank"].to_numpy()
    return r


def _leg(runner: Runner, spec, keep, name: str, per: float = 0, daily: int = 0) -> Leg:
    """Leg 와 같은 모양, 신호 행 keep(bool 배열)만."""
    p = runner.panel
    t = _sim(runner, spec, _signals(runner, spec))
    ok = (t.fill_row >= 0) & (keep[t.sig] if keep is not None else True)
    g = Leg.__new__(Leg)
    g.name, g.per, g.daily = name, per, daily
    g.code = p.codes[t.sig[ok]].astype(str)
    g.nm = p.names[t.sig[ok]].astype(str)
    g.fill = p.dates[t.fill_row[ok]]
    g.done = t.outcome_c[ok] != event.OPEN
    er = t.exit_row_c[ok]
    g.exit = np.where(er >= 0, p.dates[np.maximum(er, 0)], np.datetime64("NaT"))
    g.ret = np.where(g.done, t.ret_c[ok], 0.0)
    v = p.cols.get("value")
    g.value = v[t.sig[ok]] if v is not None else np.zeros(ok.sum())
    g.days = np.where(er >= 0, er - t.fill_row[ok], 0)
    return g


def _avg(g: Leg) -> str:
    r = g.ret[g.done]
    if not len(r):
        return "거래 없음"
    return f"{len(r):,}건 · 1건당 {r.mean():+.2f}% · 수익 비율 {(r > 0).mean() * 100:.0f}%"


def leaders(df: pd.DataFrame, kospi: pd.Series, top_n: int, look: int, filt: bool) -> pd.Series:
    """주도주 계좌 월 수익률 (월말 날짜 인덱스)."""
    d = df.dropna(subset=["ret"])
    m_ret = (1 + d["ret"]).groupby([d["code"], d["ym"]]).prod() - 1  # 종목 × 월
    mr = m_ret.unstack("code").sort_index()
    last = d.groupby("ym")["date"].transform("max")
    me = d[d["date"] == last]
    rank = me.pivot_table(index="ym", columns="code", values="cap").rank(axis=1, ascending=False)
    mom = np.expm1(np.log1p(mr.fillna(0).clip(lower=-0.99)).rolling(look).sum())
    alive = mr.notna().rolling(look).sum() >= look  # 기간 내내 거래된 종목만
    k_me = kospi.groupby(kospi.index.to_period("M")).last()
    k_ma = kospi.rolling(200).mean().groupby(kospi.index.to_period("M")).last()
    out, prev = {}, set()
    months = list(mr.index)
    for i in range(look, len(months) - 1):
        m, nxt = months[i], months[i + 1]
        ok = not filt or (m in k_me.index and k_me[m] > k_ma[m])
        if not ok:
            out[nxt] = -COST * (1.0 if prev else 0.0)  # 다 팔고 현금
            prev = set()
            continue
        cand = rank.loc[m][rank.loc[m] <= 100].index if m in rank.index else []
        sc = mom.loc[m, list(cand)][alive.loc[m, list(cand)]].dropna().sort_values(ascending=False)
        pick = set(sc.index[:top_n])
        if not pick:
            out[nxt] = 0.0
            prev = set()
            continue
        changed = len(pick - prev) / len(pick)
        r = mr.loc[nxt, list(pick)].fillna(0).mean()
        out[nxt] = r - COST * changed
        prev = pick
    s = pd.Series(out)
    s.index = s.index.to_timestamp(how="end").normalize()
    return s


def _stats(s: pd.Series) -> tuple[float, float, float]:
    eq = (1 + s).cumprod()
    years = max(len(s) / 12, 0.5)
    return float(eq.iloc[-1]), (eq.iloc[-1] ** (1 / years) - 1) * 100, float((eq / eq.cummax() - 1).min() * 100)


def run(dataset: str = "kr", log=print) -> Path:
    t0 = time.time()
    log("데이터 읽는 중…")
    panel = Panel.load(config.dataset_dir(dataset) / "prices.parquet")
    if "mktcap" not in panel.cols:
        raise SystemExit("시가총액 데이터가 없습니다.")
    if not indices_attach(panel, dataset):
        raise SystemExit("지수 데이터가 없습니다. 먼저 index-update 를 실행하세요.")
    breadth.attach(panel)
    runner = Runner(panel, {"name": dataset, "markets": ["KOSPI", "KOSDAQ"], "currency": "KRW"})
    days = np.unique(panel.dates)
    L = [f"v2b 대형주 빼기 · 대형주 신고가 · 주도주 계좌 · {dt.date.today()} · 데이터 ~{days[-1]} · 비용 차감"]

    # 1부
    log("순위 계산…")
    rk_kospi = _rank_rows(panel, ("KOSPI",))
    rk_all = _rank_rows(panel, ("KOSPI", "KOSDAQ"))
    spec = _family_spec(V2B, ["코스피", "코스닥"])
    variants = [
        ("지금 그대로 (전체)", None),
        ("코스피 상위 100 빼기", ~(rk_kospi <= 100)),
        ("코스피 상위 200 빼기", ~(rk_kospi <= 200)),
        ("코스피+코스닥 합쳐 상위 100 빼기", ~(rk_all <= 100)),
        ("(참고) 코스피 상위 100 만", rk_kospi <= 100),
    ]
    L += ["", "1부. v2b 에서 시총 상위 종목 빼기 · 5천만 · 계좌 10% × 하루 10종목 · 1회 최대 2천만 (지금 설정) · 실현 손익 기준",
          f"  {'':<30} {'최종':>8} {'연':>7} {'2021~':>7} {'최대 낙폭':>8} {'연속 손실':>14} {'노는 돈':>6}  거래"]
    for name, keep in variants:
        log(name + "…")
        g = _leg(runner, spec, keep, name)
        r = size_sim(g, days, CAPITAL, None, 0.10, 10, 20_000_000)
        L.append(f"  {name:<30} {r['final'] / 1e8:>6.2f}억 {r['cagr']:>+6.1f}% {r['oos_cagr'] or 0:>+6.1f}% "
                 f"{r['mdd']:>7.1f}% {r['worst_n']:>3}번 {r['worst_sum'] / 1e4:>7,.0f}만 {r['idle']:>5.0f}%  {_avg(g)}")
    L.append("  (연속 손실 = 손절이 이어진 가장 나쁜 구간의 건수와 합계 · 노는 돈 = 계좌 중 현금 비율 평균)")

    # 2부
    log("대형주 신고가…")
    box = dsl.evaluate(BOX_DAY, panel, runner.ev)
    nh_spec = _family_spec(NEW_HIGH, ["코스피"])
    nh = _leg(runner, nh_spec, (rk_kospi <= 100) & ~box, "대형주 신고가", 5_000_000, 2)
    L += ["", "2부. v2b + 대형주 52주 신고가 (추세장 날 · 코스피 상위 100) · 5천만 · 돈 같이 쓰기 · 고정 금액",
          f"  대형주 신고가 혼자: {_avg(nh)} · 보유 중간값 {int(np.median(nh.days[nh.done])) if nh.done.any() else 0}거래일",
          f"  {'':<40} {'연':>7} {'최대 낙폭':>8} {'최종':>8}   전략별 손익"]
    for vname, keep in (("v2b 전체", variants[0][1]), ("v2b 코스피 상위 100 빼기", variants[1][1])):
        v = _leg(runner, spec, keep, "v2b", 5_000_000, 10)
        for label, legs in ((f"{vname} 500만×10", [v]), ("  + 대형주 신고가 500만×2", [v, nh])):
            for cap_ret, tag in ((None, ""), (50.0, " · 1건 +50% 상한")):
                if cap_ret and len(legs) == 1:
                    continue
                r = portfolio(legs, CAPITAL, cap_ret)
                L.append(f"  {label + tag:<40} {r['cagr']:>+6.1f}% {r['mdd']:>7.1f}% {r['final'] / 1e8:>6.2f}억   "
                         + " · ".join(f"{g.name} {p / 1e4:+,.0f}만({n:,}건)" for g, p, n in zip(legs, r["pnl_leg"], r["taken"])))
    L.append("  (+50% 상한 = 몇 건의 큰 수익에 기대는지 보려고 1건 수익을 +50%로 자른 것)")

    # 3부
    log("주도주 계좌…")
    kospi = index_load("KOSPI")
    df = kospi_frame(panel, ("KOSPI",))
    L += ["", "3부. 주도주 계좌: 매달 말 코스피 상위 100 중 최근 n개월 가장 많이 오른 종목을 똑같이 나눠 한 달 보유",
          "  시장 필터 = 코스피가 200일선 아래로 마감한 달은 다음 달 현금 · 바꾼 비중만큼 0.25% 비용 · 배당 제외",
          f"  {'':<34} {'배수':>6} {'연':>7} {'최대 낙폭':>8}" + "".join(f"{n[:10]:>14}" for n, _, _ in PERIODS[:-1])]
    rows = []
    k_m = kospi.groupby(kospi.index.to_period("M")).last().pct_change().dropna()
    k_m.index = k_m.index.to_timestamp(how="end").normalize()
    for label, n, look, filt in (("주도주 5 · 6개월 · 필터", 5, 6, True), ("주도주 10 · 6개월 · 필터", 10, 6, True),
                                 ("주도주 10 · 12개월 · 필터", 10, 12, True), ("주도주 10 · 6개월 · 필터 없음", 10, 6, False)):
        rows.append((label, leaders(df, kospi, n, look, filt)))
    start = min(s.index[0] for _, s in rows)
    rows.insert(0, ("코스피 지수 들고 있기 (배당 제외)", k_m[k_m.index >= start]))
    for label, s in rows:
        mult, cagr, mdd = _stats(s)
        cells = []
        for _, a, b in PERIODS[:-1]:
            seg = s[(s.index >= a) & (s.index <= pd.Timestamp(b) + pd.offsets.MonthEnd(0))]
            cells.append(f"{((1 + seg).prod() - 1) * 100:+.0f}%" if len(seg) else "-")
        L.append(f"  {label:<34} {mult:>5.1f}배 {cagr:>+6.1f}% {mdd:>7.1f}%" + "".join(f"{c:>14}" for c in cells))
    L.append(f"  ({start.date()}~ 월별. 구간 칸은 그 구간 수익률)")
    # 지금 고른다면
    best = rows[2][1]
    L += ["", "  해마다 (주도주 10 · 6개월 · 필터 vs 코스피)"]
    yy = (1 + best).groupby(best.index.year).prod() - 1
    ky = (1 + rows[0][1]).groupby(rows[0][1].index.year).prod() - 1
    L.append("    " + "  ".join(f"{y} {yy[y] * 100:+.0f}/{ky.get(y, np.nan) * 100:+.0f}" for y in yy.index))
    pick = _pick_now(df, kospi, panel, 10, 6)
    L += ["", f"  이번 달 말 기준이라면 (주도주 10 · 6개월): {pick}"]
    L += ["", "읽는 법: 1부에서 '빼기'가 연 수익을 올리고 낙폭이 비슷하면 v2b 에 시총 순위 조건을 넣을 만함.",
          "2부에서 신고가를 더해 연 수익이 오르고 +50% 상한에서도 남으면 몇 종목 운이 아님.",
          "3부 주도주 계좌는 v2b 와 따로 굴리는 장기 계좌 후보: 큰 상승장(2017, 2020~21, 2025~) 몫을 얼마나 잡는지와 낙폭을 볼 것.",
          f"소요 {round((time.time() - t0) / 60, 1)}분"]
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"leader-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path


def _pick_now(df: pd.DataFrame, kospi: pd.Series, panel: Panel, top_n: int, look: int) -> str:
    """가장 최근 월말(또는 오늘) 기준 고를 종목 이름."""
    d = df.dropna(subset=["ret"])
    last_day = d["date"].max()
    now = d[d["date"] == last_day].copy()
    now["r"] = now["cap"].rank(ascending=False)
    top = now[now["r"] <= 100]["code"]
    since = last_day - pd.DateOffset(months=look)
    g = d[(d["date"] > since) & d["code"].isin(top)]
    mom = (1 + g["ret"]).groupby(g["code"]).prod() - 1
    best = mom.sort_values(ascending=False).head(top_n)
    names = dict(zip(panel.codes.astype(str), panel.names))
    above = kospi.iloc[-1] > kospi.rolling(200).mean().iloc[-1]
    return (("코스피 200일선 위 → 보유 · " if above else "코스피 200일선 아래 → 현금 · ")
            + ", ".join(f"{names.get(c, c)} {v * 100:+.0f}%" for c, v in best.items()))
