import numpy as np
import pandas as pd
import pytest

from sbt import config, research27
from sbt.data import ecos


def _months(a, b):
    return [d.strftime("%Y%m") for d in pd.date_range(a, b, freq="MS")]


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    ecos.set_key("ABCDEFGHIJ0123456789")
    rng = np.random.default_rng(0)
    apt_m = _months("1986-01", "2026-09")
    apt = 30 * np.exp(np.cumsum(rng.normal(0.004, 0.01, len(apt_m))))
    rate_m = _months("1999-05", "2026-10")
    rate = np.round(np.clip(3 + np.cumsum(rng.choice([0, 0, 0, 0.25, -0.25], len(rate_m))), 0.5, 5.25), 2)

    def call(service, *parts, n=10000, sess=None):
        if service == "StatisticTableList":
            return [{"STAT_CODE": "901Y062", "STAT_NAME": "주택매매가격지수(KB)", "SRCH_YN": "Y", "CYCLE": "M"},
                    {"STAT_CODE": "901Y999", "STAT_NAME": "주택전세가격지수(KB)", "SRCH_YN": "Y", "CYCLE": "M"},
                    {"STAT_CODE": "901Y014", "STAT_NAME": "주식시장(월)", "SRCH_YN": "Y", "CYCLE": "M"}]
        if service == "StatisticItemList":
            if parts[0] == "901Y062":
                return [{"GRP_CODE": "G1", "ITEM_CODE": "P63A", "ITEM_NAME": "총지수", "CYCLE": "M"},
                        {"GRP_CODE": "G1", "ITEM_CODE": "P63AC", "ITEM_NAME": "서울", "CYCLE": "M"},
                        {"GRP_CODE": "G2", "ITEM_CODE": "T1", "ITEM_NAME": "종합", "CYCLE": "M"},
                        {"GRP_CODE": "G2", "ITEM_CODE": "T3", "ITEM_NAME": "아파트", "CYCLE": "M"}]
            if parts[0] == "721Y001":
                return [{"ITEM_CODE": "010101000", "ITEM_NAME": "콜금리(1일, 전체거래)", "CYCLE": "M"}]
            return [{"ITEM_CODE": "K1", "ITEM_NAME": "KOSPI", "CYCLE": "M"}]
        stat, cyc, a, b, *items = parts
        if stat == "722Y001":
            return [{"TIME": t, "DATA_VALUE": str(v)} for t, v in zip(rate_m, rate) if a <= t <= b]
        if stat == "901Y062":
            assert items == ["P63AC", "T3"]  # 서울 · 아파트
            return [{"TIME": t, "DATA_VALUE": f"{v:.2f}"} for t, v in zip(apt_m, apt) if a <= t <= b]
        if stat == "721Y001":
            return [{"TIME": t, "DATA_VALUE": "5.0"} for t in _months("1990-01", "1999-12")]
        return [{"TIME": t, "DATA_VALUE": f"{500 + i:.1f}"} for i, t in enumerate(_months("1990-01", "2026-09"))]

    monkeypatch.setattr(ecos, "_call", call)
    return tmp_path


def test_update_finds_seoul_apartment(fake):
    assert ecos.update(log=lambda *a: None) == 4
    m = ecos.meta()["seoul_apt"]
    assert m["stat"] == "901Y062" and m["item_names"] == ["서울", "아파트"]
    assert ecos.load("seoul_apt").index[0] == pd.Timestamp("1986-01-01")


def test_apt_report_and_chart(fake):
    ecos.update(log=lambda *a: None)
    path = research27.run(log=lambda *a: None)
    txt = path.read_text()
    assert "2부. 금리 방향이 바뀐 달부터" in txt and "인상 시작 평균" in txt
    c = research27.chart()
    assert c["available"] and len(c["dates"]) == len(c["apt"]) == len(c["rate"])
    assert c["rate"][c["dates"].index("1990-01")] == 5.0  # 1999 전은 콜금리
    assert all(m >= "1999-05" for m, _ in c["cycles"])


def test_key_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    with pytest.raises(ecos.EcosError):
        ecos.set_key("short")
    ecos.set_key("ABCDEFGHIJ0123456789")
    assert ecos.view()["saved"] and ecos.view()["key_tail"] == "6789"
    assert oct((tmp_path / "ecos.json").stat().st_mode)[-3:] == "600"
