import datetime as dt

import pandas as pd

from sbt import config
from sbt.data import indices, yahoo_kr


def test_fill_index_from_caps(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / "index").mkdir()
    prev_day = pd.Timestamp("2026-11-20")
    indices._save("KOSDAQ", pd.Series([900.0, 1000.0], index=pd.DatetimeIndex(["2026-11-19", "2026-11-20"])))
    indices._save("KOSPI", pd.Series([3000.0, 3100.0], index=pd.DatetimeIndex(["2026-11-20", "2026-11-23"])))  # 이미 있음
    codes = [f"{i:06d}" for i in range(150)]
    prev = pd.DataFrame({"code": codes, "mktcap": [1e11] * 150}).set_index("code")
    y = pd.DataFrame({"code": codes, "market": ["KOSDAQ"] * 150, "mktcap": [0.96e11] * 150})  # 모두 -4%
    yahoo_kr.fill_index(y, prev, dt.date(2026, 11, 23), prev_day, log=lambda *a: None)
    k = indices.load("KOSDAQ")
    assert k.index[-1] == pd.Timestamp("2026-11-23") and abs(k.iloc[-1] - 960.0) < 1e-6
    assert len(indices.load("KOSPI")) == 2  # 있던 건 건드리지 않음
