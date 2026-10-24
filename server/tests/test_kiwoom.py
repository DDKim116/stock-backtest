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
