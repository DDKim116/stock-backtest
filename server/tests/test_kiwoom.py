import json
import stat

import pytest

from sbt.broker import kiwoom


class _Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code, self.text = data, status, json.dumps(data)

    def json(self):
        return self._d


@pytest.fixture
def kw(tmp_path, monkeypatch):
    monkeypatch.setattr(kiwoom.config, "DATA_DIR", tmp_path)
    kiwoom._tokens.clear()
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append((url, json, headers))
        if url.endswith("/oauth2/token"):
            if json["appkey"] == "BADKEY12345":
                return _Resp({"return_code": 3, "return_msg": "IP 인증에 실패했습니다"})
            return _Resp({"return_code": 0, "token": "TOK", "expires_dt": "20261025120000"})
        assert headers["authorization"] == "Bearer TOK" and headers["api-id"] == "ka10001"
        return _Resp({"return_code": 0, "stk_nm": "삼성전자", "cur_prc": "-70000", "flu_rt": "-1.25"})

    monkeypatch.setattr(kiwoom.requests, "post", post)
    return calls


def test_keys_and_quote(kw, tmp_path):
    with pytest.raises(kiwoom.KiwoomError):
        kiwoom.test("mock")  # 키 없음
    kiwoom.set_keys("mock", " APPKEY123456789 ", "SECRET123456789")
    assert stat.S_IMODE((tmp_path / "broker.json").stat().st_mode) == 0o600
    v = kiwoom.view()
    assert v["mock"] == {"saved": True, "key_tail": "6789", "saved_at": v["mock"]["saved_at"]} and not v["real"]["saved"]
    r = kiwoom.test("mock")
    assert r["name"] == "삼성전자" and r["price"] == 70000 and r["change_pct"] == -1.25
    assert kw[0][0] == "https://mockapi.kiwoom.com/oauth2/token" and kw[0][1]["appkey"] == "APPKEY123456789"
    kiwoom.quote("mock", "000660")
    assert sum(1 for c in kw if c[0].endswith("/oauth2/token")) == 1  # 토큰은 다시 받지 않는다
    kiwoom.clear_keys("mock")
    assert not kiwoom.view()["mock"]["saved"]


def test_errors(kw):
    with pytest.raises(kiwoom.KiwoomError, match="다시 확인"):
        kiwoom.set_keys("real", "short", "x")
    with pytest.raises(kiwoom.KiwoomError):
        kiwoom.set_keys("paper", "APPKEY123456789", "SECRET123456789")
    kiwoom.set_keys("real", "BADKEY12345", "SECRET123456789")
    with pytest.raises(kiwoom.KiwoomError, match="허용 IP"):
        kiwoom.test("real")


def test_live_parsers(kw, monkeypatch):
    calls = []

    def fake_call(mode, path, api_id, body):
        calls.append(api_id)
        if api_id == "ka10095":
            return {"atn_stk_infr": [{"stk_cd": "005930", "stk_nm": "삼성전자", "cur_prc": "-70000", "open_pric": "+71000",
                                      "high_pric": "+71500", "low_pric": "-69800", "flu_rt": "-1.25", "trde_qty": "123",
                                      "trde_prica": "8610", "mac": "4180000"}]}
        if api_id == "ka20001":
            return {"cur_prc": "+756.92", "pred_pre": "+29.71", "flu_rt": "+4.09"}
        if api_id == "ka10080":
            return {"stk_min_pole_chart_qry": [
                {"cntr_tm": "20261028091000", "cur_prc": "-70100", "open_pric": "70000", "high_pric": "70200", "low_pric": "69900", "trde_qty": "10"},
                {"cntr_tm": "20261028090500", "cur_prc": "70000", "open_pric": "71000", "high_pric": "71000", "low_pric": "69800", "trde_qty": "20"},
                {"cntr_tm": "20261027153000", "cur_prc": "71000", "open_pric": "71000", "high_pric": "71000", "low_pric": "71000", "trde_qty": "5"},
            ]}
        raise AssertionError(api_id)

    monkeypatch.setattr(kiwoom, "call", fake_call)
    q = kiwoom.quotes_full("mock", ["005930"])["005930"]
    assert q["price"] == 70000 and q["open"] == 71000 and q["low"] == 69800 and q["change_pct"] == -1.25
    assert q["value"] == 8_610_000_000 and q["mktcap"] == 418_000_000_000_000  # 백만원·억원 → 원
    assert kiwoom.quotes("mock", ["005930"]) == {"005930": 70000}
    ix = kiwoom.index_quotes("mock")
    assert ix["KOSDAQ"]["price"] == 756.92 and ix["KOSPI"]["change_pct"] == 4.09
    bars = kiwoom.minute_bars("mock", "005930", 5)
    assert [b["t"] for b in bars] == ["2026-10-28 09:05", "2026-10-28 09:10"]  # 가장 최근 날만, 오래된 순
    assert bars[0]["o"] == 71000 and bars[1]["c"] == 70100
    with pytest.raises(kiwoom.KiwoomError):
        kiwoom.minute_bars("mock", "005930", 7)


def test_throttle(monkeypatch):
    t = [100.0]
    slept = []
    monkeypatch.setattr(kiwoom.time, "monotonic", lambda: t[0])
    monkeypatch.setattr(kiwoom.time, "sleep", lambda s: slept.append(round(s, 2)))
    kiwoom._last_call.clear()
    kiwoom._throttle("kt00018")
    kiwoom._throttle("kt00018")
    assert slept == [0.4]  # 같은 순간 두 번째 호출은 0.4초 기다린다
