"""키움 REST API: 키 보관, 접근토큰, 시세 조회.

- 키는 data/broker.json (권한 600) 에만 저장하고, 화면에는 끝 4자리만 보여 준다.
- 모의투자(mockapi)와 실전(api) 키는 서로 다르다.
- 이 모듈에는 주문 기능이 없다. 자동 매매는 모의투자로 충분히 시험한 뒤 따로 만든다.
"""
from __future__ import annotations

import json
import os
import threading
import time

import requests

from .. import config

BASES = {"mock": "https://mockapi.kiwoom.com", "real": "https://api.kiwoom.com"}
MODE_LABEL = {"mock": "모의투자", "real": "실전"}
_HEADERS = {"Content-Type": "application/json;charset=UTF-8"}
_lock = threading.Lock()
_tokens: dict[str, tuple[str, float]] = {}  # mode → (token, 만료 시각)


class KiwoomError(Exception):
    pass


# ---------------------------------------------------------------- 키 보관
def _path():
    return config.DATA_DIR / "broker.json"


def _load() -> dict:
    p = _path()
    return json.loads(p.read_text()) if p.exists() else {}


def _save(d: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(d, f)
    os.chmod(p, 0o600)


def _check_mode(mode: str) -> None:
    if mode not in BASES:
        raise KiwoomError("mode 는 mock(모의투자) 또는 real(실전) 입니다")


def set_keys(mode: str, appkey: str, secretkey: str) -> None:
    _check_mode(mode)
    appkey, secretkey = appkey.strip(), secretkey.strip()
    if len(appkey) < 10 or len(secretkey) < 10 or any(c.isspace() for c in appkey + secretkey):
        raise KiwoomError("App Key·Secret Key 를 다시 확인해 주세요 (공백 없이 전체를 붙여넣기)")
    with _lock:
        d = _load()
        d.setdefault("kiwoom", {})[mode] = {"appkey": appkey, "secretkey": secretkey,
                                            "saved_at": time.strftime("%Y-%m-%d %H:%M")}
        _save(d)
        _tokens.pop(mode, None)


def clear_keys(mode: str) -> None:
    _check_mode(mode)
    with _lock:
        d = _load()
        d.get("kiwoom", {}).pop(mode, None)
        _save(d)
        _tokens.pop(mode, None)


def _keys(mode: str) -> dict:
    k = _load().get("kiwoom", {}).get(mode)
    if not k:
        raise KiwoomError(f"{MODE_LABEL[mode]} 키가 저장되어 있지 않습니다")
    return k


def view() -> dict:
    """화면용: 키는 끝 4자리만."""
    d = _load().get("kiwoom", {})
    return {m: ({"saved": True, "key_tail": d[m]["appkey"][-4:], "saved_at": d[m].get("saved_at", "")}
                if m in d else {"saved": False}) for m in BASES}


# ---------------------------------------------------------------- 호출
def _post(mode: str, path: str, body: dict, headers: dict | None = None) -> dict:
    try:
        r = requests.post(BASES[mode] + path, json=body, headers={**_HEADERS, **(headers or {})}, timeout=15)
    except requests.RequestException as e:
        raise KiwoomError(f"키움 서버에 연결하지 못했습니다: {e}") from e
    try:
        data = r.json()
    except ValueError:
        raise KiwoomError(f"키움 응답 오류 {r.status_code}: {r.text[:200]}")
    if r.status_code >= 400 or data.get("return_code", 0) not in (0, "0"):
        raise KiwoomError(_explain(data.get("return_msg") or f"HTTP {r.status_code}"))
    return data


def _explain(msg: str) -> str:
    if "IP" in msg or "ip" in msg:
        return f"{msg}\n→ 키움 사이트의 허용 IP 에 이 서버 주소가 등록되어 있는지 확인하세요."
    if "appkey" in msg.lower() or "secret" in msg.lower() or "8001" in msg or "8002" in msg:
        return f"{msg}\n→ 키가 맞는지, 모의투자 키를 실전 칸에(또는 반대로) 넣지 않았는지 확인하세요."
    return msg


def token(mode: str) -> str:
    _check_mode(mode)
    with _lock:
        cur = _tokens.get(mode)
        if cur and cur[1] > time.time() + 300:
            return cur[0]
    k = _keys(mode)
    data = _post(mode, "/oauth2/token", {"grant_type": "client_credentials",
                                         "appkey": k["appkey"], "secretkey": k["secretkey"]})
    tok = data.get("token")
    if not tok:
        raise KiwoomError(data.get("return_msg") or "토큰을 받지 못했습니다")
    exp = time.time() + 23 * 3600  # 보통 하루. expires_dt 형식이 바뀌어도 넉넉히 다시 받는다
    with _lock:
        _tokens[mode] = (tok, exp)
    return tok


def call(mode: str, path: str, api_id: str, body: dict) -> dict:
    return _post(mode, path, body, {"api-id": api_id, "authorization": f"Bearer {token(mode)}",
                                    "cont-yn": "N", "next-key": ""})


def num(v) -> float | None:
    """키움 숫자 문자열("+70000", "-1.25") → 숫자. 부호는 등락 방향 표시라 가격에서는 뗀다."""
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def quote(mode: str, code: str) -> dict:
    """종목기본정보요청(ka10001) 으로 현재가."""
    d = call(mode, "/api/dostk/stkinfo", "ka10001", {"stk_cd": code})
    price = num(d.get("cur_prc"))
    return {"code": code, "name": d.get("stk_nm", ""), "price": abs(price) if price is not None else None,
            "change_pct": num(d.get("flu_rt"))}


def test(mode: str) -> dict:
    q = quote(mode, "005930")
    return {"ok": True, "mode": mode, **q}


# ---------------------------------------------------------------- 이 서버의 바깥 주소 (허용 IP 등록용)
_ip: tuple[str, float] | None = None


def server_ip() -> str | None:
    global _ip
    if _ip and _ip[1] > time.time():
        return _ip[0]
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            ip = requests.get(url, timeout=5).text.strip()
            if ip and len(ip) <= 45:
                _ip = (ip, time.time() + 3600)
                return ip
        except requests.RequestException:
            continue
    return None
