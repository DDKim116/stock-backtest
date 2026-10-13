import datetime as dt

from sbt import krx_status


def test_status_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(krx_status.config, "DATA_DIR", tmp_path)
    v = krx_status.view(dt.date(2027, 9, 30))
    assert not v["remind"] and v["errors"] == [] and v["remind_from"] == "2027-10-01"
    assert krx_status.view(dt.date(2027, 10, 1))["remind"]
    # 지수는 성공한 적이 없으면 실패를 알리지 않는다 (첫 승인 대기)
    krx_status.record_error("index", "승인 안 됨 (KRX 401)", True)
    assert krx_status.view()["errors"] == []
    krx_status.record_ok("index")
    krx_status.record_error("index", "승인 안 됨 (KRX 401)", True)
    krx_status.record_error("stock", "인증키 오류", True)
    errs = krx_status.view()["errors"]
    assert [e["kind"] for e in errs] == ["stock", "index"] and errs[0]["auth"]
    krx_status.record_ok("stock")
    assert [e["kind"] for e in krx_status.view()["errors"]] == ["index"]
    # 연장 완료 → 12개월 뒤 만료 2주 전부터 다시 알림
    assert krx_status.set_renewed(dt.date(2027, 10, 5)) == "2028-09-20"
    assert not krx_status.view(dt.date(2027, 10, 6))["remind"]
    assert krx_status.view(dt.date(2028, 9, 20))["remind"]
