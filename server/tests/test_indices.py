import datetime as dt

import pandas as pd

from sbt.data import indices


def series():
    # 2019-12-31 100 → 2020 말 110, 2021 말 99 (2021-03 에 132 고점)
    pts = {
        "2019-12-30": 90, "2019-12-31": 100,
        "2020-01-31": 105, "2020-02-28": 95, "2020-12-30": 110,
        "2021-01-29": 121, "2021-03-31": 132, "2021-12-30": 99,
        "2022-01-05": 103.95,
    }
    return pd.Series(pts, dtype=float).rename(index=pd.Timestamp)


def test_analyze_years_months():
    r = indices.analyze(series(), today=dt.date(2022, 1, 10))
    ys = {y["year"]: y for y in r["years"]}
    assert list(ys) == [2020, 2021, 2022]  # 첫 해(2019)는 기준으로만 쓴다
    assert ys[2020]["ret"] == 10.0 and ys[2020]["base"] == 100
    assert ys[2021]["ret"] == -10.0
    assert ys[2021]["mdd"] == -25.0  # 132 → 99
    assert ys[2022]["partial"] and ys[2022]["ret"] == 5.0
    m20 = ys[2020]["months"]
    assert m20[0]["ret"] == 5.0 and m20[1]["ret"] == round((95 / 105 - 1) * 100, 2)
    assert m20[2] is None  # 데이터 없는 달
    assert m20[11]["ret"] == round((110 / 95 - 1) * 100, 2)  # 빈 달을 건너 직전 종가 기준
    assert ys[2022]["months"][0]["partial"]
    st = r["stats"]
    assert st["n"] == 2 and st["up"] == 1 and st["best"] == 2020 and st["worst"] == 2021
    assert st["cagr"] == round(((99 / 100) ** 0.5 - 1) * 100, 2)


def test_summary_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(indices.config, "DATA_DIR", tmp_path)
    out = indices.summary("kr")
    assert [x["key"] for x in out["indices"]] == ["KOSPI", "KOSDAQ"]
    assert not out["indices"][0]["available"]
    indices._save("KOSPI", series())
    out = indices.summary("kr")
    assert out["indices"][0]["available"] and out["indices"][0]["years"][0]["year"] == 2020


class _Resp:
    def __init__(self, code, body):
        self.status_code, self._b, self.text = code, body, str(body)

    def json(self):
        return self._b


class _Sess:
    def __init__(self, code=200, rows=None):
        self.code, self.rows, self.calls = code, rows or [], []

    def get(self, url, params, headers, timeout):
        self.calls.append((url, params))
        return _Resp(self.code, {"OutBlock_1": self.rows})


def test_fetch_kr_day_picks_main_index():
    rows = [{"IDX_NM": "코스피 200", "CLSPRC_IDX": "350.12"}, {"IDX_NM": "코스피", "CLSPRC_IDX": "2,601.50"}]
    s = _Sess(rows=rows)
    v = indices.fetch_kr_day(dt.date(2024, 1, 2), "k", indices.INDICES["KOSPI"], s)
    assert v == 2601.5 and s.calls[0][0].endswith("/idx/kospi_dd_trd") and s.calls[0][1]["basDd"] == "20240102"
    assert indices.fetch_kr_day(dt.date(2024, 1, 1), "k", indices.INDICES["KOSPI"], _Sess(rows=[])) is None


def test_fetch_kr_day_not_approved():
    import pytest

    with pytest.raises(indices.krx.KrxError, match="승인"):
        indices.fetch_kr_day(dt.date(2024, 1, 2), "k", indices.INDICES["KOSDAQ"], _Sess(code=401))


def test_attach_index_and_dsl(tmp_path, monkeypatch):
    import numpy as np

    from sbt.data.panel import Panel
    from sbt.engine import dsl

    monkeypatch.setattr(indices.config, "DATA_DIR", tmp_path)
    days = pd.bdate_range("2024-01-01", periods=10)
    indices._save("KOSPI", pd.Series(np.arange(100, 110, dtype=float), index=days))
    indices._save("KOSDAQ", pd.Series(np.arange(10, 0, -1, dtype=float), index=days))
    rows = []
    for code, mk in (("000010", "KOSPI"), ("000020", "KOSDAQ")):
        for d in days[2:]:  # 종목은 사흘째부터 거래
            rows.append({"date": d, "code": code, "name": code, "market": mk, "open": 10.0, "high": 11.0,
                         "low": 9.0, "close": 10.0, "volume": 100.0, "value": 1e9})
    panel = Panel(pd.DataFrame(rows))
    assert indices.attach(panel, "kr")
    ix = panel.cols["index"]
    assert ix[0] == 102 and ix[8] == 8  # 코스피 종목엔 코스피, 코스닥 종목엔 코스닥
    m = dsl.evaluate("지수 > 이전(지수, 2)", panel)
    assert m[2] and not m[10]  # 코스피는 오르고 코스닥은 내림
    assert indices.mtime("kr") > 0


def test_bars_merge_close_and_ohlc(tmp_path, monkeypatch):
    monkeypatch.setattr(indices.config, "DATA_DIR", tmp_path)
    days = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
    indices._save("KOSPI", pd.Series([100.0, 102.0, 101.0], index=days))  # KRX 종가
    pd.DataFrame({"date": ["2024-01-02", "2024-01-03"], "open": [99.0, 150.0], "high": [101.0, 160.0],
                  "low": [98.0, 149.0], "close": [100.1, 155.0]}).to_csv(indices.ohlc_path("KOSPI"), index=False)
    b = indices.bars("KOSPI")
    assert b[0] == {"t": "2024-01-02", "o": 99.0, "h": 101.0, "l": 98.0, "c": 100.0}  # 종가는 KRX 값
    assert b[1] == {"t": "2024-01-03", "o": 102.0, "h": 102.0, "l": 102.0, "c": 102.0}  # 크게 다른 야후 값은 버림
    assert b[2]["o"] == b[2]["c"] == 101.0  # 야후에 없는 날은 종가만
