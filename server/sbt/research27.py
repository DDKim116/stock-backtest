"""서울 아파트값 · 코스피 · 기준금리: 금리가 움직이면 아파트값은 어떻게 됐나, 코스피와는 누가 먼저 움직였나.

  python -m sbt.data.cli apt-test     (먼저 ecos-update)

자료: 한국은행 ECOS — 서울 아파트 매매가격지수(달마다), 기준금리(1999~, 그 전은 콜금리), 코스피 월말.
1부. 해마다: 아파트 · 코스피 등락, 연말 금리.
2부. 금리 인상·인하가 시작된 달부터 6개월·1년·2년 뒤 아파트·코스피.
3부. 누가 먼저: 아파트 1년 등락과 코스피 1년 등락을 몇 달씩 밀어 보며 같이 움직이는 정도(상관).
4부. 금리 수준(낮음·보통·높음)과 그 뒤 1년 아파트·코스피.
주의: 지수는 '같은 집 가격의 변화'를 이어 붙인 값 (실거래 평균가 아님). 물가를 빼지 않은 숫자.
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import config
from .data import ecos
from .data.indices import load as index_load


def monthly() -> pd.DataFrame:
    """달마다(그달 1일) apt · kospi(월말) · rate(기준금리, 그 전은 콜금리)."""
    apt = ecos.load("seoul_apt")
    if apt is None or apt.empty:
        raise SystemExit("서울 아파트 자료가 없습니다. 설정에 한국은행 ECOS 키를 넣고 연구 탭 'ECOS 받기'를 먼저 돌려 주세요.")
    k = ecos.load("kospi")  # 오래된 달 (ECOS)
    k2 = index_load("KOSPI")  # 최근 (앱 지수, 매일 갱신)
    if k2 is not None and len(k2):
        km = k2.groupby(k2.index.to_period("M")).last()
        km.index = km.index.to_timestamp()
        k = km if k is None else km.combine_first(k)
    r = ecos.load("base_rate")
    c = ecos.load("call_rate")
    rate = r if c is None else (c if r is None else r.combine_first(c[c.index < r.index.min()]))
    df = pd.DataFrame({"apt": apt})
    df["kospi"] = k.reindex(df.index) if k is not None else np.nan
    df["rate"] = rate.reindex(df.index).ffill() if rate is not None else np.nan
    return df.sort_index()


def cycles(rate: pd.Series) -> list[tuple[pd.Timestamp, str]]:
    """금리 방향이 바뀐 달: 인상 시작 / 인하 시작 (같은 방향 움직임은 이어진 것으로 본다)."""
    ch = rate.diff().fillna(0)
    out, last = [], 0
    for d, v in ch.items():
        if abs(v) < 0.05:
            continue
        s = 1 if v > 0 else -1
        if s != last:
            out.append((d, "인상" if s > 0 else "인하"))
            last = s
    return out


def _chg(s: pd.Series, d: pd.Timestamp, n: int) -> float | None:
    i = s.index.get_indexer([d])[0]
    if i < 0 or i + n >= len(s) or not np.isfinite(s.iloc[i]) or not np.isfinite(s.iloc[i + n]) or s.iloc[i] <= 0:
        return None
    return (s.iloc[i + n] / s.iloc[i] - 1) * 100


def _f(v: float | None, nd: int = 0) -> str:
    return "-" if v is None or not np.isfinite(v) else f"{v:+.{nd}f}%"


def chart() -> dict:
    """시장 탭 그림용: 달마다 아파트·코스피·금리 + 금리 방향이 바뀐 달."""
    try:
        df = monthly()
    except SystemExit as e:
        return {"available": False, "message": str(e)}
    br = ecos.load("base_rate")
    r = df["rate"]
    cy = cycles(r[r.index >= br.index.min()].dropna() if br is not None else r.dropna())

    def col(s: pd.Series) -> list:
        return [None if not np.isfinite(v) else round(float(v), 3) for v in s.to_numpy(dtype=float)]

    return {"available": True, "dates": [d.strftime("%Y-%m") for d in df.index], "apt": col(df["apt"]),
            "kospi": col(df["kospi"]), "rate": col(df["rate"]), "cycles": [[d.strftime("%Y-%m"), k] for d, k in cy],
            "source": ecos.meta().get("seoul_apt", {}).get("name")}


def run(log=print) -> Path:
    t0 = time.time()
    df = monthly()
    m = ecos.meta().get("seoul_apt", {})
    a, k, r = df["apt"], df["kospi"], df["rate"]
    L = [f"서울 아파트 · 코스피 · 기준금리 · {dt.date.today()} · 아파트 {a.index[0]:%Y-%m} ~ {a.index[-1]:%Y-%m}",
         f"아파트 지수: {m.get('stat', '?')} {m.get('name', '')} · {', '.join(map(str, m.get('item_names', [])))}",
         "금리: 1999년부터 한국은행 기준금리, 그 전은 콜금리 · 물가를 빼지 않은 숫자", ""]

    # 1부
    L += ["1부. 해마다 (12월 대비 12월)", f"  {'해':<6}{'아파트':>8}{'코스피':>9}{'연말 금리':>10}"]
    ye = df.groupby(df.index.year).last()
    for y in ye.index[1:]:
        pa, pk = ye.loc[y - 1, "apt"], ye.loc[y - 1, "kospi"]
        ca = (ye.loc[y, "apt"] / pa - 1) * 100 if pa else np.nan
        ck = (ye.loc[y, "kospi"] / pk - 1) * 100 if pk and np.isfinite(pk) else np.nan
        rr = ye.loc[y, "rate"]
        L.append(f"  {y:<6}{_f(ca, 1):>8}{_f(ck, 1):>9}{(f'{rr:.2f}%' if np.isfinite(rr) else '-'):>10}")
    span = (a.index[-1] - a.index[0]).days / 365.25
    L.append(f"  전체 연평균: 아파트 {((a.iloc[-1] / a.iloc[0]) ** (1 / span) - 1) * 100:+.1f}%")
    kk = k.dropna()
    if len(kk) > 24:
        sp = (kk.index[-1] - kk.index[0]).days / 365.25
        L[-1] += f" · 코스피 {((kk.iloc[-1] / kk.iloc[0]) ** (1 / sp) - 1) * 100:+.1f}% ({kk.index[0]:%Y-%m}~, 배당 제외)"

    # 2부
    L += ["", "2부. 금리 방향이 바뀐 달부터 (아파트 / 코스피)",
          f"  {'달':<9}{'바뀜':<5}{'금리':>7}{'6개월':>15}{'1년':>15}{'2년':>15}"]
    br = ecos.load("base_rate")
    cy = cycles(r[r.index >= br.index.min()].dropna() if br is not None else r.dropna())  # 콜금리는 매달 흔들려 빼고
    agg: dict[str, list] = {"인상": [], "인하": []}
    for d, kind in cy:
        cells = []
        row = []
        for n in (6, 12, 24):
            ca, ck = _chg(a, d, n), _chg(k, d, n)
            cells.append(f"{_f(ca)} / {_f(ck)}")
            row.append((ca, ck))
        agg[kind].append(row)
        L.append(f"  {d:%Y-%m}  {kind:<4}{r.loc[d]:>6.2f}%" + "".join(f"{c:>15}" for c in cells))
    for kind, rows in agg.items():
        if not rows:
            continue
        cells = []
        for j in range(3):
            va = [x[j][0] for x in rows if x[j][0] is not None]
            vk = [x[j][1] for x in rows if x[j][1] is not None]
            cells.append(f"{_f(np.mean(va) if va else None)} / {_f(np.mean(vk) if vk else None)}")
        L.append(f"  {kind} 시작 평균 ({len(rows)}번){'':<5}" + "".join(f"{c:>15}" for c in cells))

    # 3부
    L += ["", "3부. 누가 먼저 움직였나 (1년 등락끼리 상관, +n = 코스피가 n달 먼저)"]
    ya = a.pct_change(12) * 100
    yk = k.pct_change(12) * 100
    best = []
    for lag in range(-24, 25, 3):
        c = ya.corr(yk.shift(lag))
        best.append((lag, c))
    L.append("  " + " · ".join(f"{lag:+d}달 {c:+.2f}" for lag, c in best if np.isfinite(c)))
    ok = [(lag, c) for lag, c in best if np.isfinite(c)]
    if ok:
        lag, c = max(ok, key=lambda x: x[1])
        L.append(f"  가장 같이 움직인 때: {'코스피가 ' + str(lag) + '달 먼저' if lag > 0 else ('아파트가 ' + str(-lag) + '달 먼저' if lag < 0 else '같은 때')} (상관 {c:+.2f})")
    dr = r.diff(12)
    lr = [(lag, ya.corr(dr.shift(lag))) for lag in range(0, 37, 6)]
    L.append("  금리 1년 변화 → 그 뒤 아파트 1년 등락 상관: " + " · ".join(f"{lag}달 뒤 {c:+.2f}" for lag, c in lr if np.isfinite(c)))
    L.append("  (마이너스 = 금리가 오르면 그 뒤 아파트가 덜 오르거나 빠짐)")

    # 4부
    L += ["", "4부. 금리 수준과 그 뒤 1년 (달마다 평균)", f"  {'금리':<12}{'달 수':>6}{'아파트 1년 뒤':>14}{'코스피 1년 뒤':>14}"]
    fa = (a.shift(-12) / a - 1) * 100
    fk = (k.shift(-12) / k - 1) * 100
    for lo, hi, lab in ((0, 2, "2% 미만"), (2, 3.5, "2~3.5%"), (3.5, 5.5, "3.5~5.5%"), (5.5, 99, "5.5% 이상")):
        msk = (r >= lo) & (r < hi) & fa.notna()
        if msk.sum() < 6:
            continue
        L.append(f"  {lab:<12}{int(msk.sum()):>6}{_f(fa[msk].mean(), 1):>14}{_f(fk[msk].mean(), 1):>14}")
    L.append("  (금리 방향(오르는 중·내리는 중)이 수준보다 중요할 수 있어 2부와 같이 볼 것)")

    L += ["", f"지금: 서울 아파트 지수 {a.iloc[-1]:.1f} ({a.index[-1]:%Y-%m}, 1년 {_f(ya.iloc[-1], 1)}) · 기준금리 {r.iloc[-1]:.2f}%",
          "읽는 법: 2부에서 인하 시작 뒤 아파트가 꾸준히 오르고 인상 뒤 빠졌다면 '금리 방향'이 아파트의 큰 신호.",
          "3부에서 코스피가 몇 달 먼저 움직였다면 주식이 집값을 예고하는 편 (반대면 집값이 먼저).",
          f"소요 {round((time.time() - t0) / 60, 1)}분"]
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"apt-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
