"""키움 REST API: 키 보관, 접근토큰, 시세 조회.

- 키는 data/broker.json (권한 600) 에만 저장하고, 화면에는 끝 4자리만 보여 준다.
- 모의투자(mockapi)와 실전(api) 키는 서로 다르다.
- 주문은 모의투자(mock)에서만 된다 (ORDER_MODES). 실전 주문은 일부러 막아 두었다.
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


# API 별 최소 호출 간격(초). 키움 유량 제한보다 넉넉하게 (kt00018 은 2026-10-29 부터 초당 3회)
MIN_GAP = {"kt00018": 0.4}
DEFAULT_GAP = 0.25
_rate_lock = threading.Lock()
_last_call: dict[str, float] = {}


def _throttle(api_id: str) -> None:
    gap = MIN_GAP.get(api_id, DEFAULT_GAP)
    with _rate_lock:
        wait = _last_call.get(api_id, 0) + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call[api_id] = time.monotonic()


def call(mode: str, path: str, api_id: str, body: dict) -> dict:
    for attempt in range(3):
        _throttle(api_id)
        try:
            return _post(mode, path, body, {"api-id": api_id, "authorization": f"Bearer {token(mode)}",
                                            "cont-yn": "N", "next-key": ""})
        except KiwoomError as e:
            msg = str(e)
            if attempt < 2 and ("1700" in msg or "1701" in msg or "1702" in msg or "초과" in msg):
                time.sleep(1.2)  # 요청 개수 제한 → 잠시 뒤 다시
                continue
            if attempt < 2 and ("8005" in msg or "Token이 유효하지" in msg):
                with _lock:
                    _tokens.pop(mode, None)
                continue
            raise
    raise KiwoomError("키움 요청 재시도 실패")


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


def quotes_full(mode: str, codes: list[str]) -> dict[str, dict]:
    """지정종목 정보요청(ka10095): 여러 종목의 오늘 시가·고가·저가·현재가·등락률 (KRX)."""
    out: dict[str, dict] = {}
    for k in range(0, len(codes), 50):
        d = call(mode, "/api/dostk/stkinfo", "ka10095", {"stk_cd": "|".join(codes[k:k + 50])})
        for r in d.get("atn_stk_infr") or []:
            v = num(r.get("cur_prc"))
            if not v:
                continue
            a = lambda key: abs(num(r.get(key)) or 0) or None  # noqa: E731
            out[str(r.get("stk_cd", "")).replace("_AL", "").replace("_NX", "")[-6:]] = {
                "price": abs(v), "open": a("open_pric"), "high": a("high_pric"), "low": a("low_pric"),
                "change_pct": num(r.get("flu_rt")), "volume": a("trde_qty"), "value": a("trde_prica"),
                "name": r.get("stk_nm", ""), "time": r.get("cntr_tm"), "date": r.get("dt"),
            }
    return out


def quotes(mode: str, codes: list[str]) -> dict[str, float]:
    """여러 종목 현재가만."""
    return {c: q["price"] for c, q in quotes_full(mode, codes).items()}


INDEX_CODES = {"KOSPI": ("0", "001"), "KOSDAQ": ("1", "101")}


def index_quotes(mode: str) -> dict[str, dict]:
    """업종현재가요청(ka20001): 코스피·코스닥 종합지수 현재가."""
    out = {}
    for key, (mk, cd) in INDEX_CODES.items():
        d = call(mode, "/api/dostk/sect", "ka20001", {"mrkt_tp": mk, "inds_cd": cd})
        v = num(d.get("cur_prc"))
        if v:
            out[key] = {"price": abs(v), "change": num(d.get("pred_pre")), "change_pct": num(d.get("flu_rt")),
                        "time": d.get("inds_cur_prc_tm")}
    return out


def minute_bars(mode: str, code: str, scope: int = 5) -> list[dict]:
    """주식분봉차트조회(ka10080): 가장 최근 거래일의 분봉 (오래된 것부터)."""
    if scope not in (1, 3, 5, 10, 15, 30, 45, 60):
        raise KiwoomError("분봉 단위는 1·3·5·10·15·30·45·60분")
    d = call(mode, "/api/dostk/chart", "ka10080", {"stk_cd": code, "tic_scope": str(scope), "upd_stkpc_tp": "1"})
    rows = []
    for r in d.get("stk_min_pole_chart_qry") or []:
        t = str(r.get("cntr_tm", ""))
        c = num(r.get("cur_prc"))
        if len(t) < 12 or not c:
            continue
        rows.append({"t": f"{t[:4]}-{t[4:6]}-{t[6:8]} {t[8:10]}:{t[10:12]}", "c": abs(c),
                     "o": abs(num(r.get("open_pric")) or c), "h": abs(num(r.get("high_pric")) or c),
                     "l": abs(num(r.get("low_pric")) or c), "v": abs(num(r.get("trde_qty")) or 0)})
    if not rows:
        return []
    rows.sort(key=lambda x: x["t"])
    last_day = rows[-1]["t"][:10]
    return [x for x in rows if x["t"].startswith(last_day)]


# ---------------------------------------------------------------- 계좌
def cash(mode: str) -> dict:
    """예수금상세현황(kt00001): 주문가능금액 등."""
    d = call(mode, "/api/dostk/acnt", "kt00001", {"qry_tp": "3"})
    return {"deposit": num(d.get("entr")), "orderable": num(d.get("ord_alow_amt")),
            "d2": num(d.get("d2_entra"))}


def balance(mode: str) -> dict:
    """계좌평가잔고내역(kt00018): 보유 종목."""
    d = call(mode, "/api/dostk/acnt", "kt00018", {"qry_tp": "1", "dmst_stex_tp": "KRX"})
    rows = []
    for r in d.get("acnt_evlt_remn_indv_tot") or []:
        qty = num(r.get("rmnd_qty")) or 0
        if qty <= 0:
            continue
        rows.append({"code": str(r.get("stk_cd", ""))[-6:], "name": r.get("stk_nm", ""), "qty": int(qty),
                     "avg_price": abs(num(r.get("pur_pric")) or 0), "price": abs(num(r.get("cur_prc")) or 0),
                     "pnl": num(r.get("evltv_prft")), "ret": num(r.get("prft_rt"))})
    return {"total_buy": num(d.get("tot_pur_amt")), "total_eval": num(d.get("tot_evlt_amt")),
            "pnl": num(d.get("tot_evlt_pl")), "ret": num(d.get("tot_prft_rt")),
            "assets": num(d.get("prsm_dpst_aset_amt")), "holdings": rows}


def executions(mode: str) -> dict[str, dict]:
    """체결요청(ka10076) 오늘 체결을 주문번호별로 모은다: {ord_no: {qty, price, left}}."""
    d = call(mode, "/api/dostk/acnt", "ka10076", {"qry_tp": "0", "sell_tp": "0", "stex_tp": "0"})
    by: dict[str, list[dict]] = {}
    for r in d.get("cntr") or []:
        by.setdefault(str(r.get("ord_no", "")).lstrip("0") or "0", []).append(r)
    out = {}
    for no, rs in by.items():
        ordered = max(abs(num(r.get("ord_qty")) or 0) for r in rs)
        left = min(abs(num(r.get("oso_qty")) or 0) for r in rs)
        filled = ordered - left
        q = [abs(num(r.get("cntr_qty")) or 0) for r in rs]
        p = [abs(num(r.get("cntr_pric")) or 0) for r in rs]
        if sum(q) and abs(sum(q) - filled) < 0.5:  # 체결마다 한 줄
            price = sum(a * b for a, b in zip(q, p)) / sum(q)
        else:  # 주문마다 누적 한 줄
            price = p[-1]
        out[no] = {"qty": int(filled), "price": price, "left": int(left), "ordered": int(ordered),
                   "code": str(rs[0].get("stk_cd", ""))[-6:],
                   "side": "sell" if "매도" in str(rs[0].get("io_tp_nm", "")) else "buy"}
    return out


# ---------------------------------------------------------------- 주문 (모의투자만)
ORDER_MODES = {"mock"}


def _ord(mode: str, api_id: str, body: dict) -> str:
    if mode not in ORDER_MODES:
        raise KiwoomError("실전 주문은 아직 막혀 있습니다 (모의투자에서 충분히 시험한 뒤 열 예정)")
    d = call(mode, "/api/dostk/ordr", api_id, {"dmst_stex_tp": "KRX", **body})
    no = str(d.get("ord_no", "")).lstrip("0")
    if not no:
        raise KiwoomError(d.get("return_msg") or "주문번호를 받지 못했습니다")
    return no


def buy_limit(mode: str, code: str, qty: int, price: int) -> str:
    return _ord(mode, "kt10000", {"stk_cd": code, "ord_qty": str(qty), "ord_uv": str(price), "trde_tp": "0"})


def sell_limit(mode: str, code: str, qty: int, price: int) -> str:
    return _ord(mode, "kt10001", {"stk_cd": code, "ord_qty": str(qty), "ord_uv": str(price), "trde_tp": "0"})


def sell_market(mode: str, code: str, qty: int) -> str:
    return _ord(mode, "kt10001", {"stk_cd": code, "ord_qty": str(qty), "ord_uv": "", "trde_tp": "3"})


def cancel(mode: str, code: str, ord_no: str) -> str:
    return _ord(mode, "kt10003", {"orig_ord_no": ord_no.zfill(7), "stk_cd": code, "cncl_qty": "0"})


def tick(price: float) -> int:
    """KRX 호가 단위 (2023년 개편 기준, 코스피·코스닥 공통)."""
    for lim, t in ((2000, 1), (5000, 5), (20000, 10), (50000, 50), (200000, 100), (500000, 500)):
        if price < lim:
            return t
    return 1000


def round_tick(price: float, up: bool) -> int:
    import math

    t = tick(price)
    return int((math.ceil if up else math.floor)(price / t) * t)


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
