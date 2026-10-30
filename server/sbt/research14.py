"""박스피 점검: 코스피 시총 상위 100 vs 나머지, 박스장에서 통하는 전략.

  python -m sbt.data.cli box-test

1부. 코스피 보통주를 매달 말 시가총액 순위로 나눠 다음 달 수익률을 잇는다 (우선주·스팩 제외, 상장폐지 종목도 그때는 포함).
     상위 10 · 상위 100(시총 가중 ≈ 코스피 지수) · 상위 100(동일 가중) · 101위 아래(동일 가중·시총 가중).
     해마다, 구간별(박스피 2011~2016 등) 수익률.
2부. 지금 기준 '아직 갇혀 있나': 상위 100 vs 나머지 종목 중 2021-06 말·2018-01 말보다 싼 종목 비율, 최근 수익률 중간값.
3부. 박스장 전략: 코스피 종목 대상 매수 전략을 '박스장 날'(코스피 1년 최고가 ≤ 1년 최저가 × 1.25)과 '추세장 날'로 나눠
     비교하고, 상위 100 / 나머지로도 나눈다.
4부. 코스피 지수로 박스 매매: 1년 범위의 아래쪽에서 사고 위쪽에서 팔기 vs 그냥 들고 있기 (배당·현금 이자 제외).
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
from .engine import dsl
from .engine.run import Runner
from .engine.stats import summarize
from .research import _family_spec
from .research2 import Ctx, _fmt, _signals, _sim

CUT = "2021-01-01"
BOX_DAY = "최고(지수, 250) <= 최저(지수, 250) * 1.25"
PERIODS = [
    ("2011~2016 박스피", "2011-01-01", "2016-12-31"),
    ("2017 반도체 랠리", "2017-01-01", "2017-12-31"),
    ("2018~2020.2 하락·횡보", "2018-01-01", "2020-02-29"),
    ("2020.3~2021.6 코로나 뒤 급등", "2020-03-01", "2021-06-30"),
    ("2021.7~2024 다시 갇힘", "2021-07-01", "2024-12-31"),
    ("2025~지금", "2025-01-01", "2100-01-01"),
    ("2010~지금 전체", "2010-01-01", "2100-01-01"),
]
STRATS: list[tuple[str, str, str]] = [
    ("급락 반등 v2b (지금 쓰는 것)", "많이 빠진 종목의 짧은 반등", """[신호]
수익률(5) <= -15
그리고 rsi(14) <= 25
그리고 지수 <= 이전(지수, 5) * 0.97
[매수]
다음날시가
[청산]
목표 = 10
손절 = 7
보유기간 = 5"""),
    ("볼린저 하단 복귀", "볼린저 하단을 깼다가 다시 안으로 들어온 날 (평균으로 돌아오기)", """[신호]
이전(종가, 1) < 이전(이평(종가, 20) - 표준편차(종가, 20) * 2, 1)
그리고 종가 > 이평(종가, 20) - 표준편차(종가, 20) * 2
[매수]
다음날시가
[청산]
목표 = 7
손절 = 5
보유기간 = 7"""),
    ("짧은 과매도 (rsi 3)", "200일선 위의 종목이 며칠 급하게 빠진 날, 5일선 위로 오면 정리", """[신호]
rsi(3) <= 10
그리고 종가 > 이평(종가, 200)
[매수]
다음날시가
[청산]
목표 = 없음
손절 = 8
보유기간 = 10
청산조건 = 종가 > 이평(종가, 5)"""),
    ("종목 박스 하단", "1년 동안 60% 안에서 움직인 종목이 1년 최저가 근처(+10% 이내)에서 반등한 날", """[신호]
최고(고가, 250) <= 최저(저가, 250) * 1.6
그리고 종가 <= 최저(저가, 250) * 1.10
그리고 종가 > 이전(종가, 1)
[매수]
다음날시가
[청산]
목표 = 15
손절 = 8
보유기간 = 60"""),
    ("박스권 돌파", "20일 동안 15% 안에서 움직이다 거래량 2배로 위로 뚫은 날 (비교용: 추세 전략)", """[신호]
종가 > 이전(최고(고가, 20), 1)
그리고 이전(최고(고가, 20), 1) <= 이전(최저(저가, 20), 1) * 1.15
그리고 거래량 >= 이평(거래량, 20) * 2
[매수]
다음날시가
[청산]
목표 = 없음
손절 = 6
추적손절 = 8
보유기간 = 20"""),
    ("52주 신고가", "거래량 실린 52주 신고가 돌파, 50일선 깨면 정리 (비교용: 추세 전략)", """[신호]
종가 > 이전(최고(고가, 250), 1)
그리고 거래량 >= 이평(거래량, 20) * 1.5
[매수]
다음날시가
[청산]
목표 = 없음
손절 = 10
보유기간 = 120
청산조건 = 종가 < 이평(종가, 50)"""),
]


def kospi_frame(panel: Panel) -> pd.DataFrame:
    """코스피 보통주 행: 날짜·종목·일간 수익률·전날 시총·전달 말 순위."""
    p = panel
    close, cap = p.cols["close"], p.cols["mktcap"]
    prev = np.r_[np.nan, close[:-1]]
    prev_cap = np.r_[np.nan, cap[:-1]]
    first = p.pos == 0
    prev[first] = np.nan
    prev_cap[first] = np.nan
    m = (p.markets == "KOSPI") & ~p.is_preferred & ~p.is_spac & np.isfinite(cap) & (cap > 0)
    idx = np.flatnonzero(m)
    df = pd.DataFrame({"row": idx, "date": pd.DatetimeIndex(p.dates[idx]), "code": p.codes[idx].astype(str),
                       "ret": np.clip(close[idx] / prev[idx] - 1, -0.4, 0.4), "w": prev_cap[idx], "cap": cap[idx]})
    df["ym"] = df["date"].dt.to_period("M")
    # 매달 마지막 거래일 순위 → 다음 달에 쓴다
    last = df.groupby("ym")["date"].transform("max")
    me = df[df["date"] == last][["ym", "code", "cap"]].copy()
    me["rank"] = me.groupby("ym")["cap"].rank(ascending=False, method="first")
    me["ym"] = me["ym"] + 1
    df = df.merge(me[["ym", "code", "rank"]], on=["ym", "code"], how="left")
    return df


def group_returns(df: pd.DataFrame) -> pd.DataFrame:
    """날짜별 그룹 수익률 (열: 이름)."""
    d = df.dropna(subset=["rank", "ret"])
    out = {}

    def ew(g):
        return g.groupby("date")["ret"].mean()

    def cw(g):
        g = g.dropna(subset=["w"])
        return (g["ret"] * g["w"]).groupby(g["date"]).sum() / g.groupby("date")["w"].sum()

    out["상위 10 (시총 가중)"] = cw(d[d["rank"] <= 10])
    out["상위 100 (시총 가중)"] = cw(d[d["rank"] <= 100])
    out["상위 100 (동일 가중)"] = ew(d[d["rank"] <= 100])
    out["101위 아래 (시총 가중)"] = cw(d[d["rank"] > 100])
    out["101위 아래 (동일 가중)"] = ew(d[d["rank"] > 100])
    return pd.DataFrame(out).fillna(0.0)


def _pct(x: float) -> str:
    return f"{x * 100:+.0f}%"


def stuck(panel: Panel, df: pd.DataFrame) -> list[str]:
    """지금 기준 상위 100 / 나머지 종목이 예전 가격보다 싼 비율."""
    last = df["date"].max()
    now = df[df["date"] == last].copy()
    now["rank_now"] = now["cap"].rank(ascending=False, method="first")
    close = panel.cols["close"]
    now["close"] = close[now["row"].to_numpy()]
    L = [f"  기준일 {last.date()} · 코스피 보통주 {len(now):,}종목 (지금 시총 순위로 나눔)",
         f"  {'':<14} {'2021-06 말보다 쌈':>14} {'2018-01 말보다 쌈':>14} {'2015-12 말보다 쌈':>14} "
         f"{'1년 수익률 중간값':>14} {'2021-06 말 이후 중간값':>18}"]
    for label, sel in (("상위 100", now["rank_now"] <= 100), ("101~300위", (now["rank_now"] > 100) & (now["rank_now"] <= 300)),
                       ("301위 아래", now["rank_now"] > 300), ("전체", now["rank_now"] > 0)):
        g = now[sel]
        if g.empty:
            continue
        cells = []
        for when in ("2021-06-30", "2018-01-31", "2015-12-31", None):
            if when is None:
                when = (last - pd.Timedelta(days=365)).strftime("%Y-%m-%d")
            past = df[(df["date"] <= when) & df["code"].isin(g["code"])]
            ref = past.sort_values("date").groupby("code")["row"].last()
            ref_close = pd.Series(close[ref.to_numpy()], index=ref.index)
            r = g.set_index("code")["close"].reindex(ref_close.index) / ref_close - 1
            cells.append(r.dropna())
        since21 = cells[0]
        L.append(f"  {label:<14} {(since21 < 0).mean() * 100:>13.0f}% {(cells[1] < 0).mean() * 100:>13.0f}% "
                 f"{(cells[2] < 0).mean() * 100:>13.0f}% {cells[3].median() * 100:>+13.0f}% {since21.median() * 100:>+17.0f}%")
    L.append("  (그날 상장돼 있던 종목만 비교. 2021-06 은 지난 고점, 2018-01 은 그 전 고점, 2015-12 는 박스피 한가운데)")
    return L


def index_box(kospi: pd.Series) -> list[str]:
    """코스피 지수 1년 범위 안 위치로 사고팔기."""
    hi = kospi.rolling(250).max()
    lo = kospi.rolling(250).min()
    pos = (kospi - lo) / (hi - lo)
    ret = kospi.pct_change().fillna(0.0)
    out = ["  " + f"{'':<22}" + "".join(f"{name[:12]:>16}" for name, _, _ in PERIODS)]
    rules = [("그냥 들고 있기", None), ("아래 20%에서 사고 위 20%에서 팔기", (0.2, 0.8)),
             ("아래 30%에서 사고 위 30%에서 팔기", (0.3, 0.7))]
    for label, rule in rules:
        if rule is None:
            hold = pd.Series(1.0, index=kospi.index)
        else:
            state, h = 0.0, []
            for x in pos.to_numpy():
                if np.isfinite(x):
                    if x <= rule[0]:
                        state = 1.0
                    elif x >= rule[1]:
                        state = 0.0
                h.append(state)
            hold = pd.Series(h, index=kospi.index).shift(1).fillna(0.0)  # 다음 날부터 반영
        r = ret * hold
        cells = []
        for _, a, b in PERIODS:
            seg = r[(r.index >= a) & (r.index <= b)]
            if not len(seg):
                cells.append("-")
                continue
            eq = (1 + seg).cumprod()
            inv = hold[(hold.index >= a) & (hold.index <= b)].mean()
            cells.append(f"{_pct(eq.iloc[-1] - 1)}/{(eq / eq.cummax() - 1).min() * 100:.0f}%/{inv * 100:.0f}")
        out.append(f"  {label:<22}" + "".join(f"{c:>16}" for c in cells))
    out.append("  칸 = 수익률/최대 낙폭/들고 있던 날 비율(%) · 판 동안은 현금(이자 0)으로 계산, 배당 제외")
    return out


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
    ctx = Ctx(runner, np.datetime64(CUT, "D"))
    kospi = index_load("KOSPI")
    log("시총 순위로 나누는 중…")
    df = kospi_frame(panel)
    gr = group_returns(df)
    last = gr.index[-1]
    L = [f"박스피 점검 · {dt.date.today()} · 데이터 ~{last.date()} · 코스피 보통주(우선주·스팩 제외) · 매달 말 시총 순위로 다음 달 그룹",
         "", "1부. 그룹별 수익률 (배당 제외, 상장폐지 종목은 마지막 거래일까지)"]
    names = list(gr.columns)
    L.append("  " + f"{'':<26}" + "".join(f"{n[:11]:>14}" for n in names) + f"{'코스피 지수':>12}")
    for label, a, b in PERIODS:
        seg = gr[(gr.index >= a) & (gr.index <= b)]
        k = kospi[(kospi.index >= a) & (kospi.index <= b)] if kospi is not None else None
        kp = kospi[kospi.index < a] if kospi is not None else None
        k_ret = (k.iloc[-1] / (kp.iloc[-1] if kp is not None and len(kp) else k.iloc[0]) - 1) if k is not None and len(k) else np.nan
        L.append(f"  {label:<26}" + "".join(f"{_pct((1 + seg[n]).prod() - 1):>14}" for n in names) + f"{_pct(k_ret):>12}")
    L += ["", "  해마다"]
    yr = (1 + gr).groupby(gr.index.year).prod() - 1
    for y, row in yr.iterrows():
        L.append(f"  {y:<26}" + "".join(f"{_pct(row[n]):>14}" for n in names) + (" (올해 지금까지)" if y == last.year else ""))
    L += ["  상위 100(시총 가중)이 코스피 지수와 거의 같이 움직이면 계산이 맞는 것.",
          "  동일 가중 = 모든 종목에 같은 돈을 넣은 것 (작은 종목도 큰 종목과 같은 무게) → '보통 종목'의 성적",
          "", "2부. 지금 기준 아직 갇혀 있나"]
    L += stuck(panel, df)

    log("박스장 전략 비교…")
    box = dsl.evaluate(BOX_DAY, panel, runner.ev)
    rank = np.full(len(panel), np.nan)
    rank[df["row"].to_numpy()] = df["rank"].to_numpy()
    top = rank <= 100
    box_share = float(pd.Series(box[df["row"].to_numpy()], index=df["date"]).groupby(level=0).first().mean() * 100)
    L += ["", f"3부. 박스장 전략 (코스피 종목만) · 박스장 날 = {BOX_DAY} (지난 1년 고점이 저점의 1.25배 이내)",
          f"  2011~지금 날 중 박스장 날 비율 약 {box_share:.0f}% · 거래 1건당 평균(비용 차감)과 건수 · 과거 ~{CUT} / 검증 {CUT}~"]
    for name, idea, text in STRATS:
        log(f"{name}…")
        spec = _family_spec(text, ["코스피"])
        t = _sim(runner, spec, _signals(runner, spec))
        L += ["", f"■ {name} — {idea}", ctx.line("전체", ctx.stats(t, spec))]
        b, tp = box[t.sig], top[t.sig]
        known = np.isfinite(rank[t.sig])
        for label, m in (("박스장 날", b), ("추세장 날", ~b), ("박스장 · 상위 100", b & tp),
                         ("박스장 · 101위 아래", b & ~tp & known), ("추세장 · 상위 100", ~b & tp),
                         ("추세장 · 101위 아래", ~b & ~tp & known)):
            x = summarize(t, spec, m) if m.any() else None
            if x and x["n_complete"]:
                L.append(f"  {label:<18} {_fmt(x['avg_ret'], 2)} ({x['n_complete']:,}건) · 수익 비율 "
                         f"{_fmt(x.get('profit_rate'), 1, sign=False)}")
    L += ["", "4부. 코스피 지수 박스 매매 (지난 1년 범위 안 위치로 사고팔기)"]
    if kospi is not None:
        L += index_box(kospi)
    L += ["", "읽는 법: 1부에서 상위 100(시총)과 101위 아래(동일)의 차이가 '대형주만 오른 장'의 크기.",
          "3부에서 박스장 날 성적이 좋은 전략이 박스피용, 추세장 날만 좋은 전략은 박스피에선 쉬어야 할 전략.",
          f"소요 {round((time.time() - t0) / 60, 1)}분"]
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"box-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
