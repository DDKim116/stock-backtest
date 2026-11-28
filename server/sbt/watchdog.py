"""자동 매매 지킴이: 장중에 자동 매매가 몇 분째 멈춰 있으면 휴대폰으로 알린다 (서버 안 별도 타이머, 2분마다).

  python -m sbt.data.cli watchdog

- 평일 거래일 08:45~15:35 에, 자동 매매의 '마지막 확인'(20초마다 갱신)이 3분 넘게 멈춰 있으면 알림 한 번.
- 다시 돌아오면 '다시 작동' 알림 한 번. 같은 멈춤으로 여러 번 알리지 않는다.
- 서버 컴퓨터 자체가 꺼지면 이것도 같이 멈추므로 알림이 오지 않는다 (그건 키움 앱에서 직접 확인).
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from . import config, push
from .autotrade import is_trading_day

KST = ZoneInfo("Asia/Seoul")
STALE = 180  # 초


def check(store, now: dt.datetime | None = None, send=None) -> str:
    now = now or dt.datetime.now(KST).replace(tzinfo=None)
    send = send or (lambda t, b: push.send(store, t, b, tab="auto"))
    try:
        key_reminder(store, now, send)
    except Exception:  # noqa: BLE001 — 키 알림이 실패해도 멈춤 감시는 계속
        pass
    if not is_trading_day(now.date()) or not (dt.time(8, 45) <= now.time() <= dt.time(15, 35)):
        return "장 밖"
    st = store.get_setting("autotrade_state", {})
    wd = store.get_setting("watchdog", {})
    last = st.get("last_tick")
    age = (now - dt.datetime.fromisoformat(last)).total_seconds() if last else 10 ** 9
    if age > STALE:
        if not wd.get("alert"):
            mins = "기록 없음" if not last else f"{int(age // 60)}분째"
            send("⚠ 자동 매매가 멈춰 있어요", f"마지막 확인 {last[11:19] if last else '-'} ({mins}). 손절 감시도 멈춘 상태예요. "
                 "Termius 에서 'sudo systemctl restart sbt-api' 또는 키움 앱에서 보유 종목을 확인하세요.")
            store.set_setting("watchdog", {"alert": now.isoformat(timespec="seconds")})
        return f"멈춤 {int(age)}초"
    if wd.get("alert"):
        send("자동 매매가 다시 작동해요", f"{wd['alert'][11:16]} 부터 멈췄다가 {now:%H:%M} 에 돌아왔어요.")
        store.set_setting("watchdog", {})
    return "정상"


def key_reminder(store, now: dt.datetime, send) -> int:
    """키움 API 키 갱신 알림: 알림 시작일부터 하루 한 번 (평일 지킴이가 도는 시간에)."""
    from .broker import kiwoom

    sent = store.get_setting("key_remind", {})
    today = now.date().isoformat()
    n = 0
    for mode, text in kiwoom.key_reminders(now.date()):
        if sent.get(mode) == today:
            continue
        send("🔑 키움 API 키 갱신하세요", text)
        sent[mode] = today
        n += 1
    if n:
        store.set_setting("key_remind", sent)
    return n


def run() -> None:
    from .api.store import Store

    print(check(Store(config.DATA_DIR / "app.sqlite3")))
