import datetime as dt

import pandas as pd

from sbt import config
from sbt.data import indices, kiwoom_kr


def _setup(tmp_path):
    raw = tmp_path / "raw" / "2026"
    raw.mkdir(parents=True)
    codes = [f"{i:06d}" for i in range(200)]
    prev = pd.DataFrame({"code": codes, "name": [f"N{i}" for i in range(200)],
                         "market": ["KOSDAQ"] * 150 + ["KOSPI"] * 50, "open": 1000.0, "high": 1000.0, "low": 1000.0,
                         "close": 1000.0, "volume": 100.0, "value": 1e8, "mktcap": 1e11, "shares": 1e8,
                         "date": pd.Timestamp("2026-11-24")})
    prev.to_parquet(raw / "20261124.parquet", index=False)
    (tmp_path / "index").mkdir()
    indices._save("KOSDAQ", pd.Series([900.0], index=pd.DatetimeIndex(["2026-11-24"])))
    indices._save("KOSPI", pd.Series([3000.0], index=pd.DatetimeIndex(["2026-11-24"])))
    return codes


def test_kiwoom_close_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    codes = _setup(tmp_path)

    def quotes(cs):
        return {c: {"price": 960.0, "open": 990.0, "high": 1000.0, "low": 950.0, "volume": 500.0, "value": 4.8e8,
                    "mktcap": 0.96e11, "date": "20261125"} for c in cs[:190]}  # 10종목 거래정지

    ok = kiwoom_kr.update_today(tmp_path, now=dt.datetime(2026, 11, 25, 15, 45), quotes=quotes,
                                index_quotes=lambda: {"KOSPI": {"price": 2950.0}}, log=lambda *a: None)
    assert ok
    y = pd.read_parquet(tmp_path / "raw" / "2026" / "20261125.parquet")
    assert len(y) == 190 and (y["src"] == "kiwoom").all() and abs(y["chg_rt"].iloc[0] + 4.0) < 1e-9
    assert indices.load("KOSPI").iloc[-1] == 2950.0  # 키움 지수
    assert abs(indices.load("KOSDAQ").iloc[-1] - 864.0) < 1e-6  # 키움에 없으면 시총 합으로 추정 (-4%)
    assert kiwoom_kr.today_src(tmp_path, dt.date(2026, 11, 25)) == "kiwoom"
    # 다시 돌리면 건너뜀 (이미 있음)
    assert not kiwoom_kr.update_today(tmp_path, now=dt.datetime(2026, 11, 25, 15, 50), quotes=quotes, log=lambda *a: None)


def test_kiwoom_refuses_outside_window_and_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    _setup(tmp_path)
    q = lambda cs: {c: {"price": 960.0, "volume": 1.0, "date": "20261124"} for c in cs}  # noqa: E731 어제 값
    assert not kiwoom_kr.update_today(tmp_path, now=dt.datetime(2026, 11, 25, 16, 30), quotes=q, log=lambda *a: None)
    assert not kiwoom_kr.update_today(tmp_path, now=dt.datetime(2026, 11, 25, 15, 45), quotes=q, log=lambda *a: None)
