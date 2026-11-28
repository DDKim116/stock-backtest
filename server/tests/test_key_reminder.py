import datetime as dt

from sbt import config, watchdog
from sbt.api.store import Store
from sbt.broker import kiwoom


def test_key_meta_and_reminder(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    kiwoom.set_keys("real", "A" * 20, "B" * 20)
    kiwoom.set_meta("real", "2027-10-24", "2027-10-01")
    v = kiwoom.view()["real"]
    assert v["expires"] == "2027-10-24" and v["remind_from"] == "2027-10-01" and "appkey" not in v
    kiwoom.set_keys("real", "C" * 20, "D" * 20)  # 키를 바꿔도 날짜는 새로 넣을 때까지 유지
    assert kiwoom.view()["real"]["expires"] == "2027-10-24"
    assert kiwoom.key_reminders(dt.date(2027, 9, 30)) == []
    r = kiwoom.key_reminders(dt.date(2027, 10, 1))
    assert len(r) == 1 and "23일 남음" in r[0][1]
    store = Store(tmp_path / "app.sqlite3")
    sent = []
    send = lambda t, b: sent.append((t, b))  # noqa: E731
    now = dt.datetime(2027, 10, 1, 8, 0)  # 금요일 08:00 (지킴이 첫 실행)
    watchdog.check(store, now, send)
    watchdog.check(store, now + dt.timedelta(minutes=2), send)  # 같은 날은 한 번만
    assert sum("키 갱신" in t for t, _ in sent) == 1
    watchdog.check(store, dt.datetime(2027, 10, 4, 8, 0), send)  # 다음 거래일 다시
    assert sum("키 갱신" in t for t, _ in sent) == 2


def test_meta_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    kiwoom.set_keys("real", "A" * 20, "B" * 20)
    for exp, rem in (("2027/10/24", None), ("2027-10-24", "2027-11-01")):
        try:
            kiwoom.set_meta("real", exp, rem)
            raise AssertionError("should fail")
        except kiwoom.KiwoomError:
            pass
    kiwoom.set_meta("real", "", "")
    assert kiwoom.view()["real"]["expires"] is None


def test_account_test_returns_no_account_number(monkeypatch):
    monkeypatch.setattr(kiwoom, "cash", lambda m: {"deposit": 0, "orderable": 0, "acnt_no": "12348475"})
    monkeypatch.setattr(kiwoom, "balance", lambda m: {"assets": 0, "holdings": [], "acnt_no": "12348475"})
    r = kiwoom.account_test("real")
    assert r["deposit"] == 0 and r["holdings"] == 0 and "8475" not in str(r)
