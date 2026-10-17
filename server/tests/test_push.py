import base64
import datetime as dt
import json

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from sbt import push


def _ua():
    k = ec.generate_private_key(ec.SECP256R1())
    pub = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return k, push.b64u(pub), push.b64u(b"0123456789abcdef")


def _decrypt(data: bytes, ua_key, ua_pub_b64: str, auth_b64: str) -> bytes:
    """브라우저 쪽 복호화 (RFC 8291) — 암호화가 표준대로인지 확인용."""
    salt, idlen = data[:16], data[20]
    as_pub = data[21:21 + idlen]
    body = data[21 + idlen:]
    ua_pub = push.unb64u(ua_pub_b64)
    shared = ua_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    ikm = push._hkdf(push.unb64u(auth_b64), shared, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    cek = push._hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = push._hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    pt = AESGCM(cek).decrypt(nonce, body, None)
    assert pt.endswith(b"\x02")
    return pt[:-1]


def test_encrypt_roundtrip():
    k, pub, auth = _ua()
    msg = json.dumps({"title": "신호", "body": "급락 반등 v2: 3개"}, ensure_ascii=False).encode()
    assert _decrypt(push.encrypt(msg, pub, auth), k, pub, auth) == msg


def test_vapid_header(tmp_path, monkeypatch):
    monkeypatch.setattr(push.config, "DATA_DIR", tmp_path)
    key = push.vapid_key()
    assert push.vapid_key().private_numbers() == key.private_numbers()  # 한 번 만든 키를 다시 쓴다
    h = push._vapid_header("https://fcm.googleapis.com/fcm/send/abc", key)
    jwt, k = h.removeprefix("vapid t=").split(", k=")
    head, claims, sig = jwt.split(".")
    assert json.loads(push.unb64u(claims))["aud"] == "https://fcm.googleapis.com"
    raw = push.unb64u(sig)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    key.public_key().verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    assert k == push.public_key()


def test_texts():
    rows = [{"name": n} for n in "가나다라"]
    assert push.signal_text([("v2", rows), ("v1", [])], False) == "v2: 4개 · 가, 나, 다 외 1"
    assert push.signal_text([("v1", [])], False) is None
    assert push.signal_text([("v1", [])], True) == "v1: 신호 없음"
