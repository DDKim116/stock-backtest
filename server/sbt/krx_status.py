"""KRX OpenAPI 상태 기록: 마지막 성공·실패, 이용 기간 연장 알림 날짜.

data/krx_status.json 에 저장하고, 앱 맨 위 경고 띠가 이것을 읽는다.
"""
from __future__ import annotations

import datetime as dt
import json

from . import config

KINDS = {"stock": "종목 데이터", "index": "지수 데이터"}
DEFAULT_REMIND = "2027-10-01"  # 2026년 10월 신청(12개월) → 만료 전 미리 알림


def _path():
    return config.DATA_DIR / "krx_status.json"


def load() -> dict:
    try:
        return json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}


def _save(s: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1))
    tmp.replace(p)


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def record_ok(kind: str) -> None:
    s = load()
    s.setdefault(kind, {})["last_ok"] = _now()
    s[kind].pop("error", None)
    _save(s)


def record_error(kind: str, message: str, auth: bool) -> None:
    s = load()
    s.setdefault(kind, {})["error"] = {"at": _now(), "message": message[:300], "auth": auth}
    _save(s)


def set_renewed(today: dt.date | None = None) -> str:
    """연장 완료: 연장한 날부터 12개월 뒤 만료로 보고, 만료 2주 전부터 다시 알린다."""
    today = today or dt.date.today()
    s = load()
    s["remind_from"] = (today + dt.timedelta(days=365 - 14)).isoformat()
    s["renewed_at"] = _now()
    _save(s)
    return s["remind_from"]


def view(today: dt.date | None = None) -> dict:
    """앱에 보여 줄 상태. 지수는 한 번이라도 성공한 뒤의 실패만 알린다 (첫 승인 대기 중에는 조용히)."""
    today = today or dt.date.today()
    s = load()
    remind_from = s.get("remind_from", DEFAULT_REMIND)
    errors = []
    for kind, label in KINDS.items():
        k = s.get(kind) or {}
        e = k.get("error")
        if not e or (kind == "index" and not k.get("last_ok")):
            continue
        errors.append({"kind": kind, "label": label, "at": e["at"], "message": e["message"], "auth": e["auth"],
                       "last_ok": k.get("last_ok")})
    return {
        "remind": today >= dt.date.fromisoformat(remind_from),
        "remind_from": remind_from,
        "errors": errors,
    }
