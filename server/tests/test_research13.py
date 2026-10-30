import numpy as np
import pandas as pd

from sbt import research13


def _px(n=800):
    idx = pd.bdate_range("2020-01-01", periods=n)
    g = np.arange(n)
    return pd.DataFrame({"voo": 100.0 + 0 * g, "qqq": 100.0 + 0 * g, "qld": 100 * 1.004 ** g}, index=idx)


def test_rule_caps_qld_share():
    px = _px()
    end = px.index[-1]
    a = research13.simulate(px, None, px.index[0], end, "A")
    b = research13.simulate(px, None, px.index[0], end, "B")
    c = research13.simulate(px, None, px.index[0], end, "C")
    d = research13.simulate(px, None, px.index[0], end, "D")
    assert a["paid"] == b["paid"] == c["paid"] == d["paid"]
    assert "share" in a and a["value"] < c["value"] < b["value"]  # QLD 만 오르는 장
    assert d["share"] < c["share"] < b["share"]
    assert c["max_share"] > 25 and d["max_share"] > 25
