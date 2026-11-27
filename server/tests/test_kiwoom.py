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


# ---------------------------------------------------------------- 주문 중복 방지 (결과 모르면 다시 보내지 않는다)
import requests  # noqa: E402
from urllib3.exceptions import MaxRetryError, NewConnectionError, ProtocolError  # noqa: E402


@pytest.fixture
def net(tmp_path, monkeypatch):
    """가짜 키움: 경로별로 응답(또는 예외)을 차례로 돌려준다. 실제 네트워크는 쓰지 않는다."""
    monkeypatch.setattr(kiwoom.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(kiwoom.time, "sleep", lambda s: None)
    kiwoom._tokens.clear()
    kiwoom.set_keys("mock", "APPKEY123456789", "SECRET123456789")
    st = {"calls": [], "token": [], "api": []}

    def post(url, json=None, headers=None, timeout=None):
        path = url.split("kiwoom.com", 1)[1]
        st["calls"].append((path, (headers or {}).get("api-id")))
        q = st["token"] if path == "/oauth2/token" else st["api"]
        r = q.pop(0) if q else (_Resp({"return_code": 0, "token": "TOK"}) if path == "/oauth2/token"
                                else _Resp({"return_code": 0, "ord_no": "0001234"}))
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(kiwoom.requests, "post", post)
    return st


class _Html:
    def __init__(self, status):
        self.status_code, self.text = status, "<html>Bad Gateway</html>"

    def json(self):
        raise ValueError("not json")


def _refused():
    return requests.exceptions.ConnectionError(MaxRetryError(None, "/x", reason=NewConnectionError(None, "refused")))


def _aborted():
    return requests.exceptions.ConnectionError(ProtocolError("Connection aborted.", ConnectionResetError(104, "reset")))


def _orders(st):
    return [c for c in st["calls"] if c[0] == "/api/dostk/ordr"]


@pytest.mark.parametrize("err", [requests.exceptions.ReadTimeout("Read timed out"), _aborted(), _Html(502),
                                 _Resp({"return_code": 1, "return_msg": "서버 내부 오류"}, status=500)])
def test_order_not_retried_when_result_unknown(net, err):
    net["api"] = [err]
    with pytest.raises(kiwoom.KiwoomUncertain):
        kiwoom.buy_limit("mock", "005930", 1, 70000)
    assert len(_orders(net)) == 1  # 두 번 보내지 않는다


@pytest.mark.parametrize("err", [requests.exceptions.ConnectTimeout("connect timeout"), _refused(),
                                 _Resp({"return_code": 1700, "return_msg": "[1700] 허용된 요청 개수를 초과하였습니다"})])
def test_order_retried_when_surely_not_sent(net, err):
    net["api"] = [err]
    assert kiwoom.sell_market("mock", "005930", 3) == "1234"
    assert len(_orders(net)) == 2


def test_order_retried_after_token_expired(net):
    net["api"] = [_Resp({"return_code": 3, "return_msg": "[8005] Token이 유효하지 않습니다"})]
    assert kiwoom.cancel("mock", "005930", "77") == "1234"
    assert len(_orders(net)) == 2 and sum(1 for c in net["calls"] if c[0] == "/oauth2/token") == 2


def test_order_rejected_is_plain_error(net):
    net["api"] = [_Resp({"return_code": 20, "return_msg": "[2000](800033:모의투자 매도가능수량이 부족합니다.)"})]
    with pytest.raises(kiwoom.KiwoomError) as ei:
        kiwoom.sell_market("mock", "005930", 3)
    assert not isinstance(ei.value, kiwoom.KiwoomUncertain) and len(_orders(net)) == 1


def test_order_ok_without_order_no_is_unknown(net):
    net["api"] = [_Resp({"return_code": 0, "return_msg": "정상"})]
    with pytest.raises(kiwoom.KiwoomUncertain):
        kiwoom.sell_limit("mock", "005930", 3, 71000)


def test_token_failure_is_not_unknown(net):
    """토큰 받기에서 끊긴 것은 주문이 안 나간 것 → '결과 모름'이 아니다."""
    net["token"] = [requests.exceptions.ReadTimeout("t")] * 3
    with pytest.raises(kiwoom.KiwoomError) as ei:
        kiwoom.buy_limit("mock", "005930", 1, 70000)
    assert not isinstance(ei.value, kiwoom.KiwoomUncertain) and not _orders(net)


def test_query_still_retried_on_timeout_and_5xx(net):
    net["api"] = [requests.exceptions.ReadTimeout("t"), _Html(502), _Resp({"return_code": 0, "cntr": []})]
    assert kiwoom.executions("mock") == {}
    assert len([c for c in net["calls"] if c[0] == "/api/dostk/acnt"]) == 3
    net["api"] = [requests.exceptions.ReadTimeout("t")] * 3
    with pytest.raises(kiwoom.KiwoomError) as ei:
        kiwoom.balance("mock")
    assert not isinstance(ei.value, kiwoom.KiwoomUncertain)  # 조회 오류는 '결과 모름'으로 올리지 않는다


def test_not_sent_classifier_with_real_sockets():
    """실제 예외 모양 확인 (이 컴퓨터 안에서만, 프록시 설정은 무시): 연결 거부는 '안 보냄', 응답 대기 중 시간 초과는 '모름'."""
    import socket

    sess = requests.Session()
    sess.trust_env = False
    with pytest.raises(requests.RequestException) as ei:
        sess.post("http://127.0.0.1:1/x", json={}, timeout=2)
    assert kiwoom._not_sent(ei.value)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    try:
        with pytest.raises(requests.exceptions.ReadTimeout) as ei:
            sess.post(f"http://127.0.0.1:{srv.getsockname()[1]}/x", json={}, timeout=0.3)
        assert not kiwoom._not_sent(ei.value)
    finally:
        srv.close()


def test_real_orders_still_blocked():
    assert kiwoom.ORDER_MODES == {"mock"}
    with pytest.raises(kiwoom.KiwoomError, match="실전 주문"):
        kiwoom.sell_market("real", "005930", 1)


def test_query_continuation_headers_sent(net):
    """연속조회: 다음 페이지 요청에 cont-yn=Y · next-key 를 실어 보낸다 (주문 TR 은 연속조회 안 함)."""
    class _H(_Resp):
        def __init__(self, data, headers):
            super().__init__(data)
            self.headers = headers

    sent_headers = []
    orig = kiwoom.requests.post

    def post(url, json=None, headers=None, timeout=None):
        if not url.endswith("/oauth2/token"):
            sent_headers.append((headers.get("cont-yn"), headers.get("next-key")))
        return orig(url, json=json, headers=headers, timeout=timeout)

    kiwoom.requests.post = post
    try:
        net["api"] = [_H({"return_code": 0, "oso": [{"ord_no": "1", "stk_cd": "A000001", "io_tp_nm": "-매도", "ord_qty": "3",
                                                    "oso_qty": "3", "cntr_qty": "0"}]}, {"cont-yn": "Y", "next-key": "abc"}),
                      _H({"return_code": 0, "oso": [{"ord_no": "2", "stk_cd": "A000002", "io_tp_nm": "-매도", "ord_qty": "1",
                                                    "oso_qty": "1", "cntr_qty": "0"}]}, {"cont-yn": "N"})]
        assert set(kiwoom.open_orders("mock")) == {"1", "2"}
    finally:
        kiwoom.requests.post = orig
    assert sent_headers == [("N", ""), ("Y", "abc")]
    with pytest.raises(kiwoom.KiwoomError):
        kiwoom.call_rows("mock", "/api/dostk/ordr", "kt10001", {}, "x")
