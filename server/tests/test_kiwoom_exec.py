from sbt.broker import kiwoom


def _run(monkeypatch, rows):
    monkeypatch.setattr(kiwoom, "_call", lambda *a, **k: ({"cntr": rows}, {}))
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
    monkeypatch.setattr(kiwoom, "_call", lambda *a, **k: ({"oso": [
        {"ord_no": "0003340", "stk_cd": "A200710", "io_tp_nm": "-매도", "ord_qty": "286", "oso_qty": "236",
         "cntr_qty": "50", "cntr_pric": "19700"},
        {"ord_no": "", "stk_cd": "A000001", "io_tp_nm": "+매수", "ord_qty": "1"}]}, {}))
    oo = kiwoom.open_orders("mock")
    assert oo == {"3340": {"qty": 50, "price": 19700, "left": 236, "ordered": 286, "code": "200710", "side": "sell",
                           "kind": "order"}}


def test_open_orders_without_list_is_error(monkeypatch):
    """[돌돌이 4] 목록 칸(oso)이 없으면 '미체결 없음'으로 오인하지 않게 오류."""
    import pytest

    monkeypatch.setattr(kiwoom, "_call", lambda *a, **k: ({"return_code": 0, "orders": []}, {}))
    with pytest.raises(kiwoom.KiwoomError):
        kiwoom.open_orders("mock")
    monkeypatch.setattr(kiwoom, "_call", lambda *a, **k: ({"return_code": 0, "oso": []}, {}))
    assert kiwoom.open_orders("mock") == {}


def test_cancel_and_modify_rows_marked(monkeypatch):
    """[돌돌이 5] 취소·정정 주문 줄은 원주문과 구분한다 (결과 모르는 매도를 취소 줄과 짝짓지 않게)."""
    ex = _run(monkeypatch, [
        {"ord_no": "10", "stk_cd": "A200710", "io_tp_nm": "-매도", "ord_qty": "500", "oso_qty": "0", "cntr_qty": "500", "cntr_pric": "10200"},
        {"ord_no": "11", "stk_cd": "A200710", "io_tp_nm": "매도취소", "ord_qty": "500", "oso_qty": "0", "cntr_qty": "0", "cntr_pric": "0"},
        {"ord_no": "12", "stk_cd": "A200710", "io_tp_nm": "매도정정", "ord_qty": "100", "oso_qty": "100", "cntr_qty": "0", "cntr_pric": "0"}])
    assert [ex[n]["kind"] for n in ("10", "11", "12")] == ["order", "cancel", "modify"]
    monkeypatch.setattr(kiwoom, "_call", lambda *a, **k: ({"oso": [
        {"ord_no": "13", "stk_cd": "A200710", "io_tp_nm": "매수취소", "ord_qty": "5", "oso_qty": "5", "cntr_qty": "0"}]}, {}))
    assert kiwoom.open_orders("mock")["13"]["kind"] == "cancel"


def test_rate_limit_only_explicit():
    """[돌돌이 9b] 요청 개수 제한만 다시 보낸다. '매도가능수량을 초과' 같은 확정 거절이나 가격 숫자는 아니다."""
    assert kiwoom._rate_limited("[1700:허용된 요청 개수를 초과하였습니다]")
    assert kiwoom._rate_limited("API 호출 횟수를 초과했습니다")
    assert not kiwoom._rate_limited("[2000](800033:모의투자 매도가능수량을 초과합니다)")
    assert not kiwoom._rate_limited("주문가능금액 초과 · 17000원")


def test_pages_followed_with_next_key(monkeypatch):
    """[돌돌이 3차-4] 응답 헤더 cont-yn=Y 면 next-key 로 다음 페이지까지 읽는다 (체결 내역·미체결 둘 다)."""
    pages = [({"cntr": [{"ord_no": "5", "stk_cd": "A000001", "io_tp_nm": "+매수", "ord_qty": "10", "oso_qty": "0",
                         "cntr_qty": "4", "cntr_pric": "100"}]}, {"cont-yn": "Y", "next-key": "K1"}),
             ({"cntr": [{"ord_no": "5", "stk_cd": "A000001", "io_tp_nm": "+매수", "ord_qty": "10", "oso_qty": "0",
                         "cntr_qty": "6", "cntr_pric": "130"}]}, {"Cont-Yn": "N"})]
    seen = []

    def fake(mode, path, api_id, body, cont="N", nxt=""):
        seen.append((cont, nxt))
        return pages[len(seen) - 1]

    monkeypatch.setattr(kiwoom, "_call", fake)
    ex = kiwoom.executions("mock")
    assert seen == [("N", ""), ("Y", "K1")] and ex["5"]["qty"] == 10 and abs(ex["5"]["price"] - 118) < 1e-9


def test_open_orders_unknown_when_pages_incomplete_or_unparsed(monkeypatch):
    import pytest

    monkeypatch.setattr(kiwoom, "_call", lambda *a, **k: ({"oso": []}, {"cont-yn": "Y", "next-key": ""}))
    with pytest.raises(kiwoom.KiwoomError):  # 다음 페이지 키가 없으면 '모름'
        kiwoom.open_orders("mock")
    monkeypatch.setattr(kiwoom, "_call", lambda *a, **k: ({"oso": [{"주문번호": "1", "수량": "5"}]}, {}))
    with pytest.raises(kiwoom.KiwoomError):  # 줄은 있는데 하나도 해석 못 함
        kiwoom.open_orders("mock")


def test_page_limit_is_error_not_partial(monkeypatch):
    """[4차-5] 쪽수 상한(MAX_PAGES)을 넘으면 일부만 돌려주지 않고 오류 ('모름')."""
    import pytest

    monkeypatch.setattr(kiwoom, "MAX_PAGES", 3)
    calls = []

    def fake(mode, path, api_id, body, cont="N", nxt=""):
        calls.append(1)
        return {"cntr": [{"ord_no": str(len(calls)), "stk_cd": "A000001", "io_tp_nm": "+매수", "ord_qty": "1",
                          "oso_qty": "0", "cntr_qty": "1", "cntr_pric": "1"}]}, {"cont-yn": "Y", "next-key": "k"}

    monkeypatch.setattr(kiwoom, "_call", fake)
    with pytest.raises(kiwoom.KiwoomError, match="페이지"):
        kiwoom.executions("mock")
    assert len(calls) == 3 and kiwoom.MAX_PAGES == 3


def test_probe_shape_only(monkeypatch):
    """[4차-6] 키움 응답 점검: 칸 이름·줄 수·첫 줄 칸 이름·연속조회 헤더·해석한 줄 수만. 값(계좌번호·가격·메시지)은 없다."""
    import json

    def fake(mode, path, api_id, body, cont="N", nxt=""):
        assert path == "/api/dostk/acnt"  # 주문 TR 은 부르지 않는다
        if api_id == "ka10075":
            return ({"return_code": 0, "return_msg": "계좌 5512-3456 조회 완료", "acnt_no": "55123456",
                     "oso": [{"ord_no": "0003340", "stk_cd": "A200710", "io_tp_nm": "-매도", "ord_qty": "286",
                              "oso_qty": "236", "cntr_qty": "50", "cntr_pric": "19700"}]},
                    {"cont-yn": "Y", "next-key": "SECRETKEY99"})
        raise kiwoom.KiwoomError("ka10076 시험 오류")

    monkeypatch.setattr(kiwoom, "_call", fake)
    r = kiwoom.probe("mock")
    o = r["ka10075"]
    assert o["has_list"] and o["rows"] == 1 and o["parsed"] == 1 and o["cont_yn"] == "Y" and o["has_next_key"]
    assert "ord_no" in o["first_row_keys"] and "oso" in o["top_keys"] and o["lists"] == {"oso": 1}
    assert "error" in r["ka10076"]
    text = json.dumps(r, ensure_ascii=False)
    for secret in ("5512", "55123456", "SECRETKEY99", "19700", "200710", "3340"):
        assert secret not in text
