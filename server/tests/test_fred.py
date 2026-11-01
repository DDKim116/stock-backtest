import numpy as np
import pandas as pd

from sbt import config
from sbt.data import fred


def _write(tmp_path, sid, idx, vals):
    d = tmp_path / "fred"
    d.mkdir(exist_ok=True)
    pd.DataFrame({"date": idx.strftime("%Y-%m-%d"), "value": vals}).to_csv(d / f"{sid}.csv", index=False)


def test_key_and_known_lag(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    try:
        fred.set_key("short")
        assert False
    except fred.FredError:
        pass
    fred.set_key("A" * 32)
    v = fred.view()
    assert v["saved"] and v["key_tail"] == "aaaa"
    assert (tmp_path / "fred.json").stat().st_mode & 0o077 == 0
    _write(tmp_path, "UNRATE", pd.date_range("2020-01-01", periods=3, freq="MS"), [3.5, 4.4, 14.7])
    raw, known = fred.load("UNRATE"), fred.load("UNRATE", known=True)
    assert raw.index[0] == pd.Timestamp("2020-01-01") and known.index[0] == pd.Timestamp("2020-02-05")  # 한 달쯤 뒤에 앎


def test_macro_signals_and_status(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    d = pd.bdate_range("2018-01-01", "2021-12-31")
    _write(tmp_path, "T10Y2Y", d, np.where(d < "2019-06-01", 0.5, -0.2))
    _write(tmp_path, "BAA10Y", d, np.where((d > "2020-03-01") & (d < "2020-05-01"), 4.0, 2.0) + np.random.default_rng(0).normal(0, 0.05, len(d)))
    m = pd.date_range("2018-01-01", "2021-12-01", freq="MS")
    _write(tmp_path, "UNRATE", m, np.where((m >= "2020-04-01") & (m < "2021-01-01"), 12.0, 4.0))
    _write(tmp_path, "DFF", d, np.where(d < "2019-01-01", 2.4, 1.5))
    from sbt import pulse, research22

    sig = research22.signals(d)
    inv = sig["금리차 역전 (10년-2년 < 0)"]
    assert not inv[pd.Timestamp("2019-05-01")] and inv[pd.Timestamp("2019-07-01")]
    assert sig["신용 스프레드 긴장 (1년 평균 + 1표준편차 위)"][pd.Timestamp("2020-04-01")]
    assert sig["실업률 상승 (3개월 평균이 1년 저점 +0.5%p)"][pd.Timestamp("2020-07-01")]
    px = pd.Series(np.linspace(100, 200, len(d)), d)
    hold = research22.backtest(px, None, fred.load("DFF"))
    assert hold["inv"] == 100 and hold["mult"] > 1.9
    rows = {r["label"]: r for r in pulse.macro_status()}
    assert rows["장단기 금리차"]["alert"] and rows["기준금리"]["value"] == "1.50%"
