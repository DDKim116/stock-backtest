"""시장 폭: 지수(시총 가중)가 아니라 종목 전체가 실제로 어땠는지.

- 상승비율: 그날 그 시장 종목 중 전날보다 오른 종목의 비율 (%)
- 동일가중 지수: 모든 종목을 같은 비중으로 친 지수 (날마다 종목 등락률의 평균을 누적, 첫날 1000)
우선주·스팩은 뺀다. 데이터 오류로 하루 ±31% 를 넘는 등락은 ±31% 로 자른다 (국내 가격제한폭 30%).
"""
from __future__ import annotations

import numpy as np

CLIP = 0.31


def compute(panel) -> dict[str, dict]:
    """시장별 {dates, up, n, adv, ew}. adv 는 %, ew 는 지수 값."""
    c = panel.cols.get("close")
    if c is None or not len(c):
        return {}
    prev = np.empty_like(c)
    prev[0] = np.nan
    prev[1:] = c[:-1]
    prev[panel.pos == 0] = np.nan
    with np.errstate(divide="ignore", invalid="ignore"):
        ret = c / prev - 1
    ok = np.isfinite(ret) & (prev > 0) & ~panel.is_preferred & ~panel.is_spac
    ret = np.clip(ret, -CLIP, CLIP)
    out = {}
    for market in np.unique(panel.markets):
        m = panel.markets == market
        days, inv = np.unique(panel.dates[m], return_inverse=True)
        okm = ok[m]
        rm = np.where(okm, ret[m], 0.0)
        n = np.bincount(inv, weights=okm.astype(float), minlength=len(days))
        up = np.bincount(inv, weights=(okm & (rm > 0)).astype(float), minlength=len(days))
        s = np.bincount(inv, weights=rm, minlength=len(days))
        with np.errstate(divide="ignore", invalid="ignore"):
            adv = np.where(n > 0, up / n * 100, np.nan)
            mean = np.where(n > 0, s / n, 0.0)
        mean[0] = 0.0  # 첫날은 기준
        ew = 1000 * np.cumprod(1 + mean)
        out[str(market)] = {"dates": days, "up": up.astype(int), "n": n.astype(int), "adv": adv, "ew": ew}
    return out


def attach(panel) -> dict[str, dict]:
    """조건식에서 쓰도록 panel.cols['breadth'](상승비율), panel.cols['ew'](동일가중 지수)를 붙인다."""
    b = compute(panel)
    if not b:
        return b
    adv = np.full(len(panel), np.nan)
    ew = np.full(len(panel), np.nan)
    for market, x in b.items():
        m = panel.markets == market
        k = np.searchsorted(x["dates"], panel.dates[m])
        adv[m] = x["adv"][k]
        ew[m] = x["ew"][k]
    panel.cols["breadth"] = np.round(adv, 4)
    panel.cols["ew"] = ew
    panel.breadth = b
    return b
