import datetime as dt
import json

import pandas as pd

from sbt.data import build, yahoo_kr


def _prev(root, day="20261014"):
    d = root / "raw" / day[:4]
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "code": ["005930", "247540"], "name": ["삼성전자", "에코프로비엠"], "market": ["KOSPI", "KOSDAQ"],
        "open": [100.0, 200.0], "high": [110.0, 210.0], "low": [90.0, 190.0], "close": [100.0, 200.0],
        "diff": [0.0, 0.0], "chg_rt": [0.0, 0.0], "volume": [1000.0, 2000.0], "value": [1e5, 4e5],
        "mktcap": [1e6, 2e6], "shares": [10000.0, 10000.0], "date": pd.Timestamp(day),
    }).to_parquet(d / f"{day}.parquet", index=False)


def test_update_today_and_replace(tmp_path, monkeypatch):
    root = tmp_path / "kr"
    _prev(root)

    def fake(tickers, start=None, period=None):
        return {t: pd.DataFrame({"date": [pd.Timestamp("2026-10-15")], "open": [101.0], "high": [112.0],
                                 "low": [99.0], "close": [110.0], "volume": [500.0]}) for t in tickers}

    monkeypatch.setattr(yahoo_kr.us, "_download", fake)
    assert yahoo_kr.update_today(root, dt.date(2026, 10, 15), log=lambda *_: None)
    f = root / "raw" / "2026" / "20261015.parquet"
    y = pd.read_parquet(f)
    assert set(y["src"]) == {"yahoo"} and y.set_index("code").at["005930", "chg_rt"] == 10.0
    assert y.set_index("code").at["005930", "mktcap"] == 10000 * 110
    build.build(root, log=lambda *_: None)
    meta = json.loads((root / "meta.json").read_text())
    assert meta["end"] == "2026-10-15" and meta["provisional"]["date"] == "2026-10-15"
    # 공식 값이 같은 파일을 덮어쓰면 표시가 지워진다
    _prev(root, "20261015")
    yahoo_kr.clear_if_official(root)
    assert not yahoo_kr.provisional_path(root).exists()
    # 공식 자료가 있으면 다시 덮어쓰지 않는다
    assert not yahoo_kr.update_today(root, dt.date(2026, 10, 15), log=lambda *_: None)


def test_update_today_low_coverage(tmp_path, monkeypatch):
    root = tmp_path / "kr"
    _prev(root)
    monkeypatch.setattr(yahoo_kr.us, "_download", lambda tickers, start=None, period=None: {})
    assert not yahoo_kr.update_today(root, dt.date(2026, 10, 15), log=lambda *_: None)
    assert not (root / "raw" / "2026" / "20261015.parquet").exists()
    assert not yahoo_kr.update_today(root, dt.date(2026, 10, 17), log=lambda *_: None)  # 토요일
