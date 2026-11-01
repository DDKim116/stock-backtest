"""메이저 코인 매일 적립: 비트코인 · 이더리움 · 리플 · 솔라나, 그리고 VOO 와 비교.

  python -m sbt.data.cli crypto-test

데이터(야후): BTC-USD(2014-09~) · ETH-USD · XRP-USD(2017-11~) · SOL-USD(2020-04~), 환율 KRW=X.
코인은 주말에도 거래되지만 VOO 와 같은 조건이 되도록 '평일마다' 1만원씩 산다 (원화 기준, 수수료 0.07%).
1부. 비트코인 매일 1만원: 각 해 1월 시작 → 3년 뒤 · 5년 뒤 · 지금, VOO(S&P500) 같은 돈과 비교.
2부. 메이저 코인 4개 매일 1만원: 같이 있는 기간(2018~ / 2021~)에서 비교.
3부. 가격 기준 역대 최대 낙폭, 해마다 수익률.
4부. 섞기: 매일 VOO 2만 + 비트코인 0 / 2천 / 5천 / 1만 (코인 비중 0~33%).
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import pandas as pd

from . import config
from .research11 import _yf, dca
from .research12 import _won, load as load_us

COINS = {"BTC": ("비트코인", "BTC-USD"), "ETH": ("이더리움", "ETH-USD"), "XRP": ("리플", "XRP-USD"),
         "SOL": ("솔라나", "SOL-USD")}
AMT = 10_000


def load(cache: Path | None = None) -> dict[str, pd.Series]:
    cache = cache or config.DATA_DIR / "crypto.parquet"
    if cache.exists() and dt.date.fromtimestamp(cache.stat().st_mtime) == dt.date.today():
        df = pd.read_parquet(cache)
        return {c: df[c].dropna() for c in df.columns}
    s = {k: _yf(t, "2014-01-01") for k, (_, t) in COINS.items()}
    pd.DataFrame(s).to_parquet(cache)
    return s


def weekdays(s: pd.Series) -> pd.Series:
    return s[s.index.dayofweek < 5]


def _mdd(s: pd.Series) -> float:
    return float((s / s.cummax() - 1).min() * 100)


def run(log=print) -> Path:
    t0 = time.time()
    log("시세 받는 중 (야후)…")
    cs = {k: weekdays(v) for k, v in load().items() if len(v)}
    us = load_us()
    sp, fx = us["SP500"], us["KRW"]
    last = min(v.index[-1] for v in cs.values())
    L = [f"메이저 코인 매일 적립 · {dt.date.today()} · 데이터 ~{last.date()} · 평일마다 1만원 · 원화 기준(환율 반영) · 수수료 0.07%"]
    L.append("  데이터 시작: " + " · ".join(f"{COINS[k][0]} {v.index[0].date()}" for k, v in cs.items()))

    # 1부 -----------------------------------------------------------------
    btc = cs["BTC"]
    L += ["", "1부. 비트코인 매일 1만원 (각 해 1월 첫 평일 시작) vs 같은 돈 VOO(S&P500)",
          "  시작 | 3년 뒤 비트코인 / VOO | 5년 뒤 비트코인 / VOO | 지금 비트코인 (연) / VOO (연) | 비트코인 원금 대비 최악 · 평가액 낙폭"]
    for y in range(2015, last.year + 1):
        start = pd.Timestamp(f"{y}-01-01")
        if start < btc.index[0]:
            continue
        cells = []
        for n in (3, 5):
            end = pd.Timestamp(f"{y + n}-01-01") - pd.Timedelta(days=1)
            if end > last:
                cells.append("아직")
                continue
            a, b = dca(btc, fx, start, end, AMT, "day"), dca(sp, fx, start, end, AMT, "day")
            cells.append(f"{a['ret']:+.0f}% / {b['ret']:+.0f}% ({_won(a['paid'])})" if a and b else "-")
        a, b = dca(btc, fx, start, last, AMT, "day"), dca(sp, fx, start, last, AMT, "day")
        if not a or not b:
            continue
        L.append(f"  {y} | {cells[0]:<28} | {cells[1]:<28} | {_won(a['value'])} (연 {a['irr']:+.0f}%) / {_won(b['value'])} "
                 f"(연 {b['irr']:+.0f}%) · 원금 {_won(a['paid'])} | {a['worst']:+.0f}% · {a['mdd']:.0f}%")
    L.append("  원금 대비 최악 = 넣은 돈보다 가장 많이 밑돌았을 때 · 평가액 낙폭 = 쌓인 평가액이 고점에서 가장 많이 빠졌을 때")

    # 2부 -----------------------------------------------------------------
    L += ["", "2부. 메이저 코인 4개 매일 1만원 (같은 기간끼리 비교, 지금까지)",
          f"  {'':<16}" + "".join(f"{COINS[k][0]:>22}" for k in COINS) + f"{'VOO':>22}"]
    for y in (2018, 2019, 2021, 2022, 2023, 2024):
        start = pd.Timestamp(f"{y}-01-01")
        row = []
        for k in COINS:
            s = cs.get(k)
            r = dca(s, fx, start, last, AMT, "day") if s is not None and s.index[0] <= start + pd.Timedelta(days=10) else {}
            row.append(f"{_won(r['value'])}({r['ret']:+.0f}%,{r['worst']:+.0f}%)" if r else "-")
        v = dca(sp, fx, start, last, AMT, "day")
        row.append(f"{_won(v['value'])}({v['ret']:+.0f}%,{v['worst']:+.0f}%)")
        paid = v["paid"]
        L.append(f"  {y}~ 원금 {_won(paid):>6}" + "".join(f"{c:>22}" for c in row))
    L.append("  칸 = 지금 평가액(원금 대비 수익률, 원금 대비 최악)")

    # 3부 -----------------------------------------------------------------
    L += ["", "3부. 가격 기준 (달러) 역대 최대 낙폭과 해마다 수익률"]
    for k, s in cs.items():
        full = load()[k]
        yr = full.groupby(full.index.year).last().pct_change().dropna() * 100
        L.append(f"  {COINS[k][0]:<6} 최대 낙폭 {_mdd(full):.0f}% · 지금 고점 대비 {(full.iloc[-1] / full.max() - 1) * 100:+.0f}% · "
                 + " ".join(f"{y} {v:+.0f}%" for y, v in yr.items()))
    spy = sp[sp.index >= btc.index[0]]
    yr = spy.groupby(spy.index.year).last().pct_change().dropna() * 100
    L.append(f"  {'S&P500':<6} 최대 낙폭 {_mdd(spy):.0f}% · " + " ".join(f"{y} {v:+.0f}%" for y, v in yr.items()))

    # 4부 -----------------------------------------------------------------
    L += ["", "4부. 섞기: 매일 VOO 2만 + 비트코인 (지금까지, 원화)",
          f"  {'':<10}" + "".join(f"{(f'+BTC {m}천' if m < 10 else '+BTC 1만') if m else 'VOO만':>26}" for m in (0, 2, 5, 10))]
    for y in (2016, 2018, 2020, 2021, 2022):
        start = pd.Timestamp(f"{y}-01-01")
        v = dca(sp, fx, start, last, 20_000, "day")
        cells = []
        for m in (0, 2, 5, 10):
            if m == 0:
                tot, paid = v["v"], v["paid"]
            else:
                b = dca(btc, fx, start, last, m * 1000, "day")
                tot = v["v"].add(b["v"].reindex(v["v"].index, method="ffill"), fill_value=0)
                paid = v["paid"] + b["paid"]
            cells.append(f"{_won(tot.iloc[-1])}({(tot.iloc[-1] / paid - 1) * 100:+.0f}%,낙폭{_mdd(tot):.0f}%)")
        L.append(f"  {y}~      " + "".join(f"{c:>26}" for c in cells))
    L += ["  칸 = 지금 평가액(원금 대비 수익률, 평가액 최대 낙폭)",
          "", "읽는 법: 비트코인이 VOO 보다 많이 벌었더라도 '원금 대비 최악'과 '낙폭'을 같이 볼 것 — 그 구간을 버텨야 그 수익이 나온다.",
          "주의: 코인 역사가 짧고(비트코인 야후 2014~, 솔라나 2020~) 과거의 폭발적 상승이 반복된다는 보장은 없다.",
          f"소요 {round((time.time() - t0) / 60, 1)}분"]
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"crypto-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
