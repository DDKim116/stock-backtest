import json
import sys
import types

import numpy as np
import pandas as pd

from sbt import config, pulse


def test_build_us_map(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    days = pd.bdate_range("2026-08-01", periods=40)
    rows = []
    for code, name, px, vol in (("AAPL", "Apple Inc. - Common Stock", 200, 5e7), ("SPY", "SPDR S&P 500 ETF Trust", 600, 9e7),
                                ("MSFT", "Microsoft Corporation - Common Stock", 400, 3e7)):
        for i, d in enumerate(days):
            c = px * (1 + 0.01 * i)
            rows.append({"date": d, "code": code, "name": name, "close": c, "value": c * vol})
    root = tmp_path / "us"
    root.mkdir()
    pd.DataFrame(rows).to_parquet(root / "prices.parquet")

    class T:
        def __init__(self, c):
            self.fast_info = {"shares": {"AAPL": 15e9, "MSFT": 7.4e9}.get(c)}

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=T))
    pulse.build_us_map(root, log=lambda *a: None)
    m = pulse.us_map()
    assert [x["code"] for x in m["items"]] == ["AAPL", "MSFT"]  # ETF 빠짐, 시총 순
    assert m["items"][0]["d1"] == round((1.39 / 1.38 - 1) * 100, 2)
    assert json.loads((tmp_path / "us_shares.json").read_text())["AAPL"] == 15e9
    assert np.isclose(m["items"][0]["cap"], 15e9 * 200 * 1.39)
