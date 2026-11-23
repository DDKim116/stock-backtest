import numpy as np
import pandas as pd

from sbt.data.panel import Panel
from sbt.pulse import _caps, treemap


def _panel():
    days = pd.bdate_range("2026-11-02", "2026-11-23")
    rows = []
    for k in range(8):
        code = f"00{k:04d}0"
        for i, d in enumerate(days):
            last = i == len(days) - 1
            if last and k >= 3:  # 마지막 날엔 3종목만 들어옴 (나머지는 하루 전까지)
                continue
            cap = (k + 1) * 1e12 * (1 + i / 100)
            if last or (k == 1 and i >= len(days) - 8):  # 마지막 날 시총 빔 · 한 종목은 일주일 넘게 빔
                cap = np.nan
            rows.append({"date": d, "code": code, "name": f"N{k}", "market": "KOSPI", "open": 1, "high": 1,
                         "low": 1, "close": 100 + i, "volume": 1, "value": 1, "mktcap": cap})
    return Panel(pd.DataFrame(rows).sort_values(["code", "date"]).reset_index(drop=True))


def test_missing_mktcap_filled_from_latest_known():
    p = _panel()
    r = p.rows_for("0000010")[-1]
    out = _caps(p, np.array([r]))
    assert np.isfinite(out[0]) and out[0] > 0


def test_treemap_shows_whole_market_even_if_last_day_partial():
    t = treemap(_panel(), "KOSPI", 50)
    assert len(t["items"]) == 8 and t["as_of"] == "2026-11-23"
    assert t["items"][0]["name"] == "N7"  # 시총 큰 순
