"""시장 쏠림: 시가총액 상위 종목이 시장 전체 시총에서 차지하는 비율 (한국, 종목 데이터의 일별 시총으로).

- 우선주는 보통주와 한 회사로 합친다 (예: 삼성전자 + 삼성전자우). 코드 앞 5자리가 같으면 같은 회사.
- 매월 마지막 거래일 기준 상위 1·5·10·30 비중, 해마다 마지막 거래일의 상위 10 종목.
- 상장폐지 종목도 그 시점에 있었으면 들어간다 (생존 편향 없음).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TOPS = (1, 5, 10, 30)


def analyze(panel, market: str) -> dict:
    if "mktcap" not in panel.cols:
        return {"market": market, "available": False}
    cap = panel.cols["mktcap"]
    m = (panel.markets == market) & np.isfinite(cap) & (cap > 0)
    if not m.any():
        return {"market": market, "available": False}
    dates = panel.dates[m]
    days = np.unique(dates)
    s = pd.Series(days, index=pd.DatetimeIndex(days))
    month_end = s.groupby(s.index.to_period("M")).max().to_numpy()
    year_end = set(s.groupby(s.index.year).max().to_numpy())
    pick = np.isin(dates, month_end)
    idx = np.flatnonzero(m)[pick]
    codes = panel.codes[idx].astype(str)
    pref = panel.is_preferred[idx]
    df = pd.DataFrame({
        "date": panel.dates[idx],
        "co": [c[:5] + "0" if p else c for c, p in zip(codes, pref)],
        "pref": pref,
        "name": panel.names[idx],
        "cap": cap[idx],
    })
    last = days[-1]
    months, years = [], []
    for d, g in df.groupby("date", sort=True):
        names = g.sort_values("pref").drop_duplicates("co").set_index("co")["name"]  # 보통주 이름 먼저
        caps = g.groupby("co")["cap"].sum().sort_values(ascending=False)
        total = float(caps.sum())
        cum = caps.cumsum().to_numpy() / total * 100
        row = {"date": str(d)[:10], "total": total, "n": int(len(caps))}
        for k in TOPS:
            row[f"top{k}"] = round(float(cum[min(k, len(cum)) - 1]), 2)
        months.append(row)
        if d in year_end:
            top = [{"code": co, "name": str(names[co]), "cap": float(v), "w": round(float(v) / total * 100, 2)}
                   for co, v in caps.head(10).items()]
            years.append({**row, "year": int(str(d)[:4]), "partial": d == last, "top": top})
    return {"market": market, "available": True, "as_of": str(last)[:10], "months": months, "years": years[::-1]}
