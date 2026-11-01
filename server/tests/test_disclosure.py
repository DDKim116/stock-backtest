import pandas as pd

from sbt.research24 import clusters, dedupe
from sbt.research25 import attach_prelim


def test_clusters_need_two_people_within_30_days():
    b = pd.DataFrame({"code": ["A", "A", "A", "B", "B"], "who": ["x", "x", "y", "x", "y"],
                      "date": pd.to_datetime(["2024-01-01", "2024-01-05", "2024-01-20", "2024-01-01", "2024-03-01"])})
    c = clusters(b)
    assert list(c["code"]) == ["A"] and c["date"].iloc[0] == pd.Timestamp("2024-01-20")


def test_dedupe_keeps_first_in_30_days():
    e = pd.DataFrame({"code": ["A"] * 3, "date": pd.to_datetime(["2024-01-01", "2024-01-20", "2024-03-01"])})
    assert list(dedupe(e)["date"].dt.day) == [1, 1]


def test_prelim_between_quarter_end_and_report():
    ev = pd.DataFrame({"code": ["A", "A", "B"], "fq": [2024 * 4 + 1, 2024 * 4 + 2, 2024 * 4 + 1],
                       "rcept": pd.to_datetime(["2024-05-15", "2024-08-14", "2024-05-15"])})
    pre = pd.DataFrame({"code": ["A", "A", "A"], "date": pd.to_datetime(["2024-03-20", "2024-04-25", "2024-09-01"])})
    e = attach_prelim(ev, pre)
    assert e["prelim"].iloc[0] == pd.Timestamp("2024-04-25")  # 분기 끝 전(3/20)은 지난 분기 것
    assert pd.isna(e["prelim"].iloc[1])  # 정기보고서 뒤(9/1)는 안 씀
    assert pd.isna(e["prelim"].iloc[2])
