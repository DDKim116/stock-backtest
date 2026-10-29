import numpy as np
import pandas as pd
import pytest

from sbt import breadth
from sbt.data.panel import Panel
from sbt.engine import dsl


def panel():
    rows = []
    closes = {"000010": [10, 11, 12], "000020": [10, 9, 9], "000030": [10, 10, 20], "000035": [10, 50, 50]}  # 우선주는 뺀다
    for code, cs in closes.items():
        for d, c in zip(pd.bdate_range("2024-01-01", periods=3), cs):
            rows.append({"date": d, "code": code, "name": code, "market": "KOSPI", "open": c, "high": c, "low": c,
                         "close": float(c), "volume": 1.0, "value": 1.0, "mktcap": 1.0})
    return Panel(pd.DataFrame(rows))


def test_breadth_and_dsl():
    p = panel()
    b = breadth.attach(p)["KOSPI"]
    assert np.isnan(b["adv"][0])
    assert b["adv"][1] == pytest.approx(100 / 3) and b["adv"][2] == pytest.approx(200 / 3)
    # 둘째 날 평균 등락 (10% - 10% + 0) / 3 = 0, 셋째 날 (12/11-1 + 0 + 31% 로 자름) / 3
    assert b["ew"][1] == pytest.approx(1000)
    assert b["ew"][2] == pytest.approx(1000 * (1 + (12 / 11 - 1 + 0.31) / 3))
    cond = dsl.evaluate("상승비율 >= 50", p)
    assert cond.sum() == 4  # 셋째 날, 종목 4개 (우선주도 그 시장 값을 받는다)
