import pandas as pd

from sbt import concentration
from sbt.data.panel import Panel


def test_top_share_merges_preferred():
    rows = []
    for d in ("2024-12-27", "2024-12-30", "2025-01-02"):
        for code, name, cap in (("005930", "삼성", 60.0), ("005935", "삼성우", 10.0), ("000660", "하이닉스", 20.0),
                                ("035420", "네이버", 10.0)):
            rows.append({"date": pd.Timestamp(d), "code": code, "name": name, "market": "KOSPI", "open": 1.0,
                         "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0, "value": 1.0, "mktcap": cap})
        rows.append({"date": pd.Timestamp(d), "code": "091990", "name": "코닥", "market": "KOSDAQ", "open": 1.0,
                     "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0, "value": 1.0, "mktcap": 999.0})
    r = concentration.analyze(Panel(pd.DataFrame(rows)), "KOSPI")
    assert [m["date"] for m in r["months"]] == ["2024-12-30", "2025-01-02"]  # 월말(마지막 거래일)만
    m = r["months"][0]
    assert m["n"] == 3 and m["top1"] == 70.0 and m["top5"] == 100.0  # 삼성 + 삼성우 = 70
    y = r["years"]
    assert [x["year"] for x in y] == [2025, 2024] and y[0]["partial"] and not y[1]["partial"]
    assert y[1]["top"][0] == {"code": "005930", "name": "삼성", "cap": 70.0, "w": 70.0}
