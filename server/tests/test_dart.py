import pandas as pd

from sbt.data import dart


def test_parse_and_quarterly():
    rows = []
    for reprt, amt, add, per, rc in (
        ("11013", "100", "", "2023.01.01 ~ 2023.03.31", "20230515000001"),
        ("11012", "120", "220", "2023.04.01 ~ 2023.06.30", "20230814000001"),
        ("11014", "130", "", "2023.07.01 ~ 2023.09.30", "20231114000001"),
        ("11011", "500", "", "2023.01.01 ~ 2023.12.31", "20240320000001"),
    ):
        rows.append({"stock_code": "005930", "corp_code": "00126380", "bsns_year": "2023", "reprt_code": reprt,
                     "fs_div": "CFS", "account_nm": "매출액", "thstrm_amount": amt, "thstrm_add_amount": add,
                     "thstrm_dt": per, "rcept_no": rc})
    rows.append(dict(rows[0], account_nm="자산총계"))  # 관심 없는 계정은 버린다
    rows.append(dict(rows[0], fs_div="OFS", thstrm_amount="1"))  # 연결이 있으면 별도는 안 쓴다
    raw = dart.parse(rows)
    assert len(raw) == 5 and raw["amount"].iloc[0] == 100
    q = dart.quarterly(raw).set_index("q")
    assert list(q["amount"]) == [100, 120, 130, 150]  # 3분기는 3개월 금액 + 앞 누적, 4분기 = 1년 - 3분기 누적
    assert q.loc[4, "rcept"] == pd.Timestamp("2024-03-20")


def test_key_rules(tmp_path, monkeypatch):
    from sbt import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    try:
        dart.set_key("short")
        assert False
    except dart.DartError:
        pass
    dart.set_key("a" * 40)
    v = dart.view()
    assert v["saved"] and v["key_tail"] == "aaaa" and "key" not in v
    assert (tmp_path / "dart.json").stat().st_mode & 0o077 == 0
    dart.clear_key()
    assert not dart.view()["saved"]


def test_missing_quarters_not_full_year():
    rows = [{"stock_code": "000660", "corp_code": "x", "bsns_year": "2015", "reprt_code": "11011", "fs_div": "CFS",
             "account_nm": "매출액", "thstrm_amount": "400", "thstrm_add_amount": "", "thstrm_dt": "2015.01.01 ~ 2015.12.31",
             "rcept_no": "20160320000001"}]
    q = dart.quarterly(dart.parse(rows))
    assert len(q) == 1 and pd.isna(q["amount"].iloc[0]) and q["cum"].iloc[0] == 400  # 1년 매출은 남지만 4분기 금액은 모름
