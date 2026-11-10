import numpy as np
import pandas as pd

from sbt.data.panel import Panel
from sbt.research28 import find


def test_finds_third_touch_breakout():
    # 400일: 올라가다 100에서 첫 고점 → 85 되돌림 → 99.5 두 번째 고점 → 90 → 102 돌파
    path = list(np.linspace(60, 100, 260)) + list(np.linspace(100, 85, 30)) + list(np.linspace(85, 99.5, 30)) \
        + list(np.linspace(99.5, 90, 20)) + list(np.linspace(90, 99, 15)) + [102] + [103] * 44
    c = np.array(path) * 100  # 1,000원 이상
    n = len(c)
    df = pd.DataFrame({"date": pd.bdate_range("2015-01-01", periods=n), "code": "000100", "name": "가", "market": "KOSPI",
                       "open": c, "high": c, "low": c, "close": c, "volume": 1e6, "value": 5e9, "mktcap": 1e12})
    p = Panel(df)
    ev = find(p)
    assert len(ev) == 1
    r = ev.iloc[0]
    assert abs(r["R"] - 10_000) < 1 and c[int(r["row"])] == 10_200
