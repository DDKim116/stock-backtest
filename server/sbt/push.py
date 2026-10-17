"""휴대폰 웹 푸시 (Web Push: RFC 8291 암호화 + RFC 8292 VAPID).

외부 푸시 라이브러리 없이 cryptography 만으로 보낸다. 서버 키(VAPID)는 data/push_vapid.pem 에 처음 한 번 만든다.
구독(기기)과 설정은 Store 의 settings 에 저장한다 (혼자 쓰는 앱이라 기기 몇 대뿐).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import time
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import config

SUBJECT = "mailto:sbt@stock-backtest.invalid"  # 푸시 서비스가 문제 있을 때 연락할 곳 (형식만 맞으면 됨)


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _raw_pub(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


# ---------------------------------------------------------------- 서버 키 (VAPID)
def _key_path():
    return config.DATA_DIR / "push_vapid.pem"


def vapid_key() -> ec.EllipticCurvePrivateKey:
    p = _key_path()
    if p.exists():
        return serialization.load_pem_private_key(p.read_bytes(), password=None)
    key = ec.generate_private_key(ec.SECP256R1())
    p.parent.mkdir(parents=True, exist_ok=True)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    return key


def public_key() -> str:
    """브라우저 구독에 쓰는 서버 공개키 (applicationServerKey)."""
    return b64u(_raw_pub(vapid_key().public_key()))


def _vapid_header(endpoint: str, key: ec.EllipticCurvePrivateKey) -> str:
    u = urlsplit(endpoint)
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    claims = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600,
                              "sub": SUBJECT}).encode())
    signing = f"{head}.{claims}".encode()
    r, s = decode_dss_signature(key.sign(signing, ec.ECDSA(hashes.SHA256())))
    jwt = f"{head}.{claims}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
    return f"vapid t={jwt}, k={b64u(_raw_pub(key.public_key()))}"


# ---------------------------------------------------------------- 암호화 (aes128gcm)
def _hkdf(salt: bytes, ikm: bytes, info: bytes, n: int) -> bytes:
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:n]


def encrypt(payload: bytes, p256dh: str, auth: str) -> bytes:
    ua_pub = unb64u(p256dh)
    secret = unb64u(auth)
    as_key = ec.generate_private_key(ec.SECP256R1())
    as_pub = _raw_pub(as_key.public_key())
    shared = as_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub))
    ikm = _hkdf(secret, shared, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    salt = os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    body = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack(">I", 4096) + bytes([len(as_pub)]) + as_pub + body


# ---------------------------------------------------------------- 보내기
class Gone(Exception):
    """구독이 끝남 (앱 삭제·알림 끔 등). 목록에서 지운다."""


def send_one(sub: dict, payload: dict, ttl: int = 12 * 3600) -> None:
    data = encrypt(json.dumps(payload, ensure_ascii=False).encode(), sub["keys"]["p256dh"], sub["keys"]["auth"])
    r = requests.post(sub["endpoint"], data=data, timeout=15, headers={
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(ttl),
        "Urgency": "normal",
        "Authorization": _vapid_header(sub["endpoint"], vapid_key()),
    })
    if r.status_code in (404, 410):
        raise Gone()
    if r.status_code >= 400:
        raise RuntimeError(f"푸시 서비스 오류 {r.status_code}: {r.text[:200]}")


def send(store, title: str, body: str, tab: str = "", tag: str = "") -> dict:
    """등록된 모든 기기로 보낸다. 끝난 구독은 지운다."""
    subs = store.get_setting("push_subs", [])
    ok, errors, keep = 0, [], []
    for s in subs:
        try:
            send_one(s, {"title": title, "body": body, "tab": tab, "tag": tag})
            ok += 1
            keep.append(s)
        except Gone:
            continue
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
            keep.append(s)
    if len(keep) != len(subs):
        store.set_setting("push_subs", keep)
    return {"sent": ok, "errors": errors}


def add_sub(store, sub: dict, device: str = "") -> None:
    if not sub.get("endpoint", "").startswith("https://") or not sub.get("keys", {}).get("p256dh"):
        raise ValueError("알림 구독 정보가 올바르지 않습니다")
    subs = [s for s in store.get_setting("push_subs", []) if s["endpoint"] != sub["endpoint"]]
    subs.append({"endpoint": sub["endpoint"], "keys": {"p256dh": sub["keys"]["p256dh"], "auth": sub["keys"]["auth"]},
                 "device": device[:80], "created": time.strftime("%Y-%m-%d %H:%M")})
    store.set_setting("push_subs", subs)


def remove_sub(store, endpoint: str) -> None:
    store.set_setting("push_subs", [s for s in store.get_setting("push_subs", []) if s["endpoint"] != endpoint])


DEFAULT_PREFS = {"paper": True, "signals": [], "signals_empty": False}


def prefs(store) -> dict:
    return {**DEFAULT_PREFS, **store.get_setting("push_prefs", {})}


# ---------------------------------------------------------------- 알림 문구
def signal_text(results: list[tuple[str, list[dict]]], empty: bool) -> str | None:
    """[(전략 이름, 그날 신호 행)] → 알림 본문. 신호가 하나도 없고 empty=False 면 None."""
    lines = []
    for name, rows in results:
        if not rows and not empty:
            continue
        if not rows:
            lines.append(f"{name}: 신호 없음")
            continue
        names = ", ".join(r["name"] for r in rows[:3])
        more = f" 외 {len(rows) - 3}" if len(rows) > 3 else ""
        lines.append(f"{name}: {len(rows)}개 · {names}{more}")
    return "\n".join(lines) if lines else None


def news_key(it: dict) -> str:
    return f"{it['book_id']}:{it['trade_id']}:{it['kind']}:{it['date']}"


def news_text(items: list[dict]) -> str:
    lines = [f"{it['name']} · {it['text']}" + (f" ({it['book']})" if it.get("book") else "") for it in items[:6]]
    if len(items) > 6:
        lines.append(f"외 {len(items) - 6}건")
    return "\n".join(lines)
