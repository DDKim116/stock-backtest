from sbt.broker import kiwoom


def _run(monkeypatch, rows):
    monkeypatch.setattr(kiwoom, "call", lambda *a, **k: {"cntr": rows})
    return kiwoom.executions("mock")


def test_cancelled_order_not_counted_as_filled(monkeypatch):
    # 목표가 286주 지정가 → 100주 체결 뒤 나머지 취소: 미체결 0 이지만 체결은 100주
    ex = _run(monkeypatch, [{"ord_no": "3340", "stk_cd": "A200710", "io_tp_nm": "-매도", "ord_qty": "286",
                             "oso_qty": "0", "cntr_qty": "100", "cntr_pric": "19735"}])
    assert ex["3340"]["qty"] == 100 and ex["3340"]["price"] == 19735


def test_cancelled_without_fill(monkeypatch):
    ex = _run(monkeypatch, [{"ord_no": "77", "stk_cd": "A000001", "io_tp_nm": "-매도", "ord_qty": "286",
                             "oso_qty": "0", "cntr_qty": "0", "cntr_pric": "0"}])
    assert ex["77"]["qty"] == 0


def test_per_fill_rows_and_open_remainder(monkeypatch):
    ex = _run(monkeypatch, [
        {"ord_no": "5", "stk_cd": "A000001", "io_tp_nm": "+매수", "ord_qty": "10", "oso_qty": "4", "cntr_qty": "2", "cntr_pric": "100"},
        {"ord_no": "5", "stk_cd": "A000001", "io_tp_nm": "+매수", "ord_qty": "10", "oso_qty": "4", "cntr_qty": "4", "cntr_pric": "130"}])
    assert ex["5"]["qty"] == 6 and abs(ex["5"]["price"] - 120) < 1e-9 and ex["5"]["left"] == 4


def test_open_orders_parser(monkeypatch):
    monkeypatch.setattr(kiwoom, "call", lambda *a, **k: {"oso": [
        {"ord_no": "0003340", "stk_cd": "A200710", "io_tp_nm": "-매도", "ord_qty": "286", "oso_qty": "236",
         "cntr_qty": "50", "cntr_pric": "19700"},
        {"ord_no": "", "stk_cd": "A000001", "io_tp_nm": "+매수", "ord_qty": "1"}]})
    oo = kiwoom.open_orders("mock")
    assert oo == {"3340": {"qty": 50, "price": 19700, "left": 236, "ordered": 286, "code": "200710", "side": "sell",
                           "kind": "order"}}


def test_open_orders_without_list_is_error(monkeypatch):
    """[돌돌이 4] 목록 칸(oso)이 없으면 '미체결 없음'으로 오인하지 않게 오류."""
    import pytest

    monkeypatch.setattr(kiwoom, "call", lambda *a, **k: {"return_code": 0, "orders": []})
    with pytest.raises(kiwoom.KiwoomError):
        kiwoom.open_orders("mock")
    monkeypatch.setattr(kiwoom, "call", lambda *a, **k: {"return_code": 0, "oso": []})
    assert kiwoom.open_orders("mock") == {}


def test_cancel_and_modify_rows_marked(monkeypatch):
    """[돌돌이 5] 취소·정정 주문 줄은 원주문과 구분한다 (결과 모르는 매도를 취소 줄과 짝짓지 않게)."""
    ex = _run(monkeypatch, [
        {"ord_no": "10", "stk_cd": "A200710", "io_tp_nm": "-매도", "ord_qty": "500", "oso_qty": "0", "cntr_qty": "500", "cntr_pric": "10200"},
        {"ord_no": "11", "stk_cd": "A200710", "io_tp_nm": "매도취소", "ord_qty": "500", "oso_qty": "0", "cntr_qty": "0", "cntr_pric": "0"},
        {"ord_no": "12", "stk_cd": "A200710", "io_tp_nm": "매도정정", "ord_qty": "100", "oso_qty": "100", "cntr_qty": "0", "cntr_pric": "0"}])
    assert [ex[n]["kind"] for n in ("10", "11", "12")] == ["order", "cancel", "modify"]
    monkeypatch.setattr(kiwoom, "call", lambda *a, **k: {"oso": [
        {"ord_no": "13", "stk_cd": "A200710", "io_tp_nm": "매수취소", "ord_qty": "5", "oso_qty": "5", "cntr_qty": "0"}]})
    assert kiwoom.open_orders("mock")["13"]["kind"] == "cancel"


def test_rate_limit_only_explicit():
    """[돌돌이 9b] 요청 개수 제한만 다시 보낸다. '매도가능수량을 초과' 같은 확정 거절이나 가격 숫자는 아니다."""
    assert kiwoom._rate_limited("[1700:허용된 요청 개수를 초과하였습니다]")
    assert kiwoom._rate_limited("API 호출 횟수를 초과했습니다")
    assert not kiwoom._rate_limited("[2000](800033:모의투자 매도가능수량을 초과합니다)")
    assert not kiwoom._rate_limited("주문가능금액 초과 · 17000원")
