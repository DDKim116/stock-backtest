"""대상승장 계좌: 낙폭을 줄이면서 큰 상승장을 길게 먹을 수 있나.

  python -m sbt.data.cli bull-plan

매일 단위로 굴린다 (배당 제외, 판 동안 현금 이자 0).
- 코스피 지수(코스피200 ETF 대신): 그냥 보유 / 200일선 위에서만 보유 (매일 또는 월말 확인). ETF 사고팔 때 0.05%.
- 주도주: 매달 말 코스피 시총 상위 100 중 최근 12(또는 6)개월 가장 많이 오른 종목을 똑같이 나눠 산다.
  시장 필터는 매일(코스피가 200일선 아래로 마감하면 다음 날 다 팔고, 위로 오면 다시 산다).
  종목별 정리: 산 값보다 -15% 또는 50일선 아래로 마감하면 그 종목만 팔고 그달은 현금.
  주식 살 때 0.015%, 팔 때 0.23%.
- 섞기: 지수(200일선)와 주도주를 매달 말 정한 비율로 다시 맞춘다.
신호는 그날 종가로 보고 그 종가에 사고판 것으로 계산 (실제는 다음 날 시가라 조금 다를 수 있음).
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import config
from .data.indices import load as index_load
from .data.panel import Panel
from .research14 import PERIODS, kospi_frame

BUY, SELL, ETF = 0.00015, 0.0023, 0.0005


def picks_by_month(df: pd.DataFrame, top_n: int, look: int) -> dict:
    """{월(Period): 그달 말에 고른 종목 코드 목록} — 다음 달에 들고 간다."""
    d = df.dropna(subset=["ret"])
    mr = ((1 + d["ret"]).groupby([d["code"], d["ym"]]).prod() - 1).unstack("code").sort_index()
    last = d.groupby("ym")["date"].transform("max")
    me = d[d["date"] == last]
    rank = me.pivot_table(index="ym", columns="code", values="cap").rank(axis=1, ascending=False)
    mom = np.expm1(np.log1p(mr.fillna(0).clip(lower=-0.99)).rolling(look).sum())
    alive = mr.notna().rolling(look).sum() >= look
    out = {}
    for m in mr.index[look:]:
        if m not in rank.index:
            continue
        cand = list(rank.loc[m][rank.loc[m] <= 100].index)
        sc = mom.loc[m, cand][alive.loc[m, cand]].dropna().sort_values(ascending=False)
        out[m] = list(sc.index[:top_n])
    return out


def index_path(k: pd.Series, mode: str) -> pd.Series:
    """코스피 지수 일간 수익률 경로. mode: hold / daily / monthly (200일선 필터)."""
    r = k.pct_change().fillna(0.0)
    if mode == "hold":
        return r
    on = k > k.rolling(200).mean()
    if mode == "monthly":
        per = k.index.to_period("M")
        month_on = on.groupby(per).last().shift(1)  # 지난달 말 상태로 이번 달
        on = pd.Series(month_on.reindex(per).to_numpy(), index=k.index).fillna(False).astype(bool)
        pos = on.astype(float)
    else:
        pos = on.shift(1).fillna(False).astype(float)  # 오늘 종가로 보고 다음 날부터
    trade = pos.diff().abs().fillna(0.0) * ETF
    return r * pos - trade


def leader_path(R: pd.DataFrame, C: pd.DataFrame, MA: pd.DataFrame, k_on: pd.Series, picks: dict,
                stop: float | None, ma_exit: bool) -> tuple[pd.Series, float]:
    """주도주 계좌 일간 수익률과 평균 투자 비율."""
    days = R.index
    hold: dict[str, float] = {}  # 종목 → 평가액
    entry: dict[str, float] = {}
    stopped: set[str] = set()
    eq = 1.0
    cash = 1.0
    out = np.zeros(len(days))
    invested = np.zeros(len(days))
    cur: list[str] = []
    Rv, Cv, Mv = R.to_numpy(), C.to_numpy(), MA.to_numpy()
    col = {c: j for j, c in enumerate(R.columns)}

    def buy_all(codes: list[str], i: int) -> None:
        nonlocal cash
        codes = [c for c in codes if c not in hold and c not in stopped and np.isfinite(Cv[i, col[c]])]
        if not codes or cash <= 0:
            return
        each = cash / len(codes)
        for c in codes:
            hold[c] = each * (1 - BUY)
            entry[c] = Cv[i, col[c]]
        cash = 0.0

    def sell(c: str) -> None:
        nonlocal cash
        cash += hold.pop(c) * (1 - SELL)
        entry.pop(c, None)

    for i, t in enumerate(days):
        start = eq
        # 오늘 수익
        for c in list(hold):
            r = Rv[i, col[c]]
            if np.isfinite(r):
                hold[c] *= 1 + r
        # 새 달: 지난달 말에 고른 종목으로 바꾼다
        if i > 0 and t.to_period("M") != days[i - 1].to_period("M"):
            new = picks.get(days[i - 1].to_period("M"), [])
            stopped = set()
            for c in list(hold):
                if c not in new:
                    sell(c)
            cur = new
        on = bool(k_on.iloc[i - 1]) if i > 0 else False  # 어제 종가 기준
        if not on and hold:
            for c in list(hold):
                sell(c)
        elif on and cur:
            buy_all(cur, i)
        # 종가로 종목별 정리
        for c in list(hold):
            j = col[c]
            px = Cv[i, j]
            if not np.isfinite(px):
                continue
            if (stop and px <= entry[c] * (1 - stop)) or (ma_exit and np.isfinite(Mv[i, j]) and px < Mv[i, j]):
                sell(c)
                stopped.add(c)
        eq = cash + sum(hold.values())
        out[i] = eq / start - 1
        invested[i] = 1 - cash / eq if eq > 0 else 0.0
    return pd.Series(out, index=days), float(invested.mean())


def mix(a: pd.Series, b: pd.Series, wa: float) -> pd.Series:
    """두 경로를 매달 말 wa : 1-wa 로 다시 맞춘다."""
    idx = a.index.intersection(b.index)
    a, b = a.reindex(idx).fillna(0), b.reindex(idx).fillna(0)
    va, vb, out = wa, 1 - wa, []
    per = idx.to_period("M")
    for i in range(len(idx)):
        if i > 0 and per[i] != per[i - 1]:
            tot = va + vb
            va, vb = tot * wa, tot * (1 - wa)
        prev = va + vb
        va *= 1 + a.iloc[i]
        vb *= 1 + b.iloc[i]
        out.append((va + vb) / prev - 1)
    return pd.Series(out, index=idx)


def _row(label: str, r: pd.Series, inv: float | None) -> str:
    eq = (1 + r).cumprod()
    years = max((r.index[-1] - r.index[0]).days / 365.25, 0.5)
    cagr = (eq.iloc[-1] ** (1 / years) - 1) * 100
    mdd = float((eq / eq.cummax() - 1).min() * 100)
    cells = []
    for _, a, b in PERIODS[:-1]:
        seg = r[(r.index >= a) & (r.index <= b)]
        cells.append(f"{((1 + seg).prod() - 1) * 100:+.0f}%" if len(seg) else "-")
    return (f"  {label:<34} {eq.iloc[-1]:>5.1f}배 {cagr:>+6.1f}% {mdd:>7.1f}% {'' if inv is None else f'{inv * 100:.0f}%':>5}"
            + "".join(f"{c:>13}" for c in cells))


def run(dataset: str = "kr", log=print) -> Path:
    t0 = time.time()
    log("데이터 읽는 중…")
    panel = Panel.load(config.dataset_dir(dataset) / "prices.parquet")
    if "mktcap" not in panel.cols:
        raise SystemExit("시가총액 데이터가 없습니다.")
    k = index_load("KOSPI")
    if k is None:
        raise SystemExit("코스피 지수 데이터가 없습니다. 먼저 index-update 를 실행하세요.")
    df = kospi_frame(panel, ("KOSPI",))
    df["close"] = panel.cols["close"][df["row"].to_numpy()]
    log("종목 표 만드는 중…")
    R = df.pivot_table(index="date", columns="code", values="ret")
    C = df.pivot_table(index="date", columns="code", values="close").reindex(R.index)
    MA = C.rolling(50, min_periods=50).mean()
    k = k.reindex(R.index.union(k.index)).ffill().reindex(R.index)
    k_on = k > k.rolling(200).mean()
    p12, p6 = picks_by_month(df, 10, 12), picks_by_month(df, 5, 12)
    p5_6 = picks_by_month(df, 5, 6)
    start = min(p12).to_timestamp(how="end") + pd.Timedelta(days=1)

    rows: list[tuple[str, pd.Series, float | None]] = []
    for label, mode in (("코스피 그냥 보유", "hold"), ("코스피 · 200일선 (매일 확인)", "daily"), ("코스피 · 200일선 (월말 확인)", "monthly")):
        rows.append((label, index_path(k, mode), None))
    legs = {}
    for key, label, picks, stop, ma in (
        ("L1", "주도주 10 · 시장 필터만", p12, None, False),
        ("L2", "주도주 10 · + 종목 -15% 정리", p12, 0.15, False),
        ("L3", "주도주 10 · + 종목 50일선 정리", p12, None, True),
        ("L4", "주도주 10 · + -15% 와 50일선 둘 다", p12, 0.15, True),
        ("L5", "주도주 5 · + 50일선 정리", p6, None, True),
        ("L6", "주도주 5 (6개월) · + 50일선 정리", p5_6, None, True),
    ):
        log(label + "…")
        s, inv = leader_path(R, C, MA, k_on, picks, stop, ma)
        legs[key] = s
        rows.append((label, s, inv))
    idx_d = index_path(k, "daily")
    rows.append(("섞기: 코스피(200일선) 50 + 주도주 10(50일선) 50", mix(idx_d, legs["L3"], 0.5), None))
    rows.append(("섞기: 코스피(200일선) 70 + 주도주 5(50일선) 30", mix(idx_d, legs["L5"], 0.7), None))

    L = [f"대상승장 계좌 · {dt.date.today()} · 데이터 ~{R.index[-1].date()} · {start.date()}~ 매일 · 배당·현금 이자 제외",
         "주도주 = 매달 말 코스피 시총 상위 100 중 최근 12개월(표시 없으면) 가장 많이 오른 종목 · 시장 필터 = 코스피 200일선 (매일)",
         "", f"  {'':<34} {'배수':>6} {'연':>7} {'최대 낙폭':>8} {'투자':>5}" + "".join(f"{n[:10]:>13}" for n, _, _ in PERIODS[:-1])]
    for label, s, inv in rows:
        L.append(_row(label, s[s.index >= start], inv))
    L += ["  투자 = 계좌 중 주식에 들어가 있던 비율 평균 (나머지는 현금)", "", "해마다 (코스피 그냥 보유 / 코스피 200일선 / 주도주 10 50일선 / 섞기 50:50)"]
    ys = [rows[0][1], rows[1][1], legs["L3"], rows[-2][1]]
    yy = [(1 + s[s.index >= start]).groupby(s[s.index >= start].index.year).prod() - 1 for s in ys]
    L.append("    " + "  ".join(f"{y} " + "/".join(f"{x.get(y, np.nan) * 100:+.0f}" for x in yy) for y in yy[0].index))

    # 지금 상태
    last = R.index[-1]
    ma200 = k.rolling(200).mean().iloc[-1]
    names = dict(zip(panel.codes.astype(str), panel.names))
    cur = p12.get(last.to_period("M") - 1, [])
    L += ["", f"지금 ({last.date()}): 코스피 {k.iloc[-1]:,.0f} · 200일선 {ma200:,.0f} → "
              + ("위 (보유)" if k.iloc[-1] > ma200 else f"아래 (현금, 200일선까지 {(ma200 / k.iloc[-1] - 1) * 100:+.1f}%)"),
          "  이번 달 주도주 10 (지난달 말 기준, ○ = 50일선 위 · × = 아래): "
          + ", ".join(f"{names.get(c, c)} {'○' if C[c].iloc[-1] >= MA[c].iloc[-1] else '×'}" for c in cur if c in C)]
    L += ["", "읽는 법: 주도주가 지수보다 많이 벌면서 최대 낙폭이 지수(-35% 안팎)에 가까우면 대상승장 계좌로 쓸 만함.",
          "섞기는 초대형주 랠리(2025~)를 지수로 따라가면서 주도주로 더 버는 절충안.",
          f"소요 {round((time.time() - t0) / 60, 1)}분"]
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"bull-plan-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
