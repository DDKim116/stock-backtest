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
