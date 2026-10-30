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


def test_dip_buys_more_when_below_cost():
    idx = pd.bdate_range("2020-01-01", periods=600)
    g = np.arange(600)
    qld = np.where(g < 300, 100 * 0.997 ** g, 100 * 0.997 ** 300 * 1.004 ** (g - 300))  # 떨어졌다 오름
    px = pd.DataFrame({"voo": 100.0 + 0 * g, "qqq": 100.0 + 0 * g, "qld": qld}, index=idx)
    d = research13.simulate(px, None, idx[0], idx[-1], "D")
    e = research13.simulate(px, None, idx[0], idx[-1], "E")
    f = research13.simulate(px, None, idx[0], idx[-1], "F")
    assert d["extra"] == 0 and e["extra"] > 0 and e["extra"] == f["extra"] and e["paid"] == f["paid"] > d["paid"]
    assert e["value"] > f["value"]  # 싸게 산 QLD 가 반등
    assert e["irr"] > f["irr"]
