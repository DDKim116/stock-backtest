import datetime as dt

from sbt import watchdog
from sbt.api.store import Store


def test_alert_once_and_recovery(tmp_path):
    store = Store(tmp_path / "app.sqlite3")
    sent = []
    send = lambda t, b: sent.append(t)  # noqa: E731
    now = dt.datetime(2026, 11, 17, 10, 0)
    store.set_setting("autotrade_state", {"last_tick": "2026-11-17T09:55:00"})
    assert watchdog.check(store, now, send).startswith("멈춤") and len(sent) == 1
    watchdog.check(store, now + dt.timedelta(minutes=2), send)
    assert len(sent) == 1  # 같은 멈춤은 한 번만
    store.set_setting("autotrade_state", {"last_tick": "2026-11-17T10:03:50"})
    assert watchdog.check(store, now + dt.timedelta(minutes=4), send) == "정상"
    assert sent[-1] == "자동 매매가 다시 작동해요"
    assert watchdog.check(store, dt.datetime(2026, 11, 17, 20, 0), send) == "장 밖"
    assert watchdog.check(store, dt.datetime(2026, 11, 15, 10, 0), send) == "장 밖"  # 일요일
