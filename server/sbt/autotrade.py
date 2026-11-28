"""자동 매매 (키움 모의투자). 여러 전략을 한 계좌에서, 전략마다 백테스트와 같은 규칙으로 주문을 낸다.

하루 흐름 (거래일, 한국시간)
- 새 거래일 데이터가 들어오면 (저녁 임시값이든 아침 공식 값이든 먼저 온 쪽) 다음 거래일 '매수 후보'를 만든다.
  켜진 전략마다 그날 신호 → 이미 보유·대기 중인 종목 제외 → 거래대금 작은 순 → 그 전략의 하루 최대 n종목.
  승인 모드면 휴대폰으로 알리고 08:45 까지 승인을 기다린다. 승인 안 한 것은 사지 않는다.
- 08:50 매수 (계좌 돈은 전략들이 같이 쓴다. 모자라면 전략 순서·후보 순서대로):
  · '다음날시가' 전략: 지정가 = 신호일 종가 × (1 + buy_limit_pct%) — 장전 동시호가라 시가가 그 아래면 시가 체결.
  · '지정가' 전략 (예: 중간값): 그 지정가로, 유효기간 동안 매일 아침 다시 주문 (KRX 지정가는 그날만 유효).
  같은 시각, 보유 종목의 목표가 매도를 다시 건다. 목표·손절·보유기간은 그 종목을 산 전략의 규칙.
- 09:00~15:19 체결 확인 (체결되면 목표가 매도 주문), 손절가 이하면 시장가 매도.
- 14:30 보유기간이 끝난 종목은 시장가 매도 (모의투자는 체결이 느려 한 시간 먼저). 마감까지 남은 수량은 다음 거래일 장 시작에 이어서.
- 15:31 마무리: 안 된 매수 주문은 '미체결'로, 목표가 주문은 내일 다시.

'전체 중지'(enabled=false)는 새 매수만 멈춘다. 이미 산 종목의 목표가·손절·기간 만료 매도는 계속한다.
수익률은 수수료·세금을 빼기 전 값이다.
"""
from __future__ import annotations

import datetime as dt
import re
import threading
import time
from typing import Callable

from .api.store import _lock
from .broker.kiwoom import KiwoomUncertain

# KRX 휴장일 (주말 제외). 틀려도 그날 주문이 거부될 뿐이고, 보유일 계산은 실제 시세 데이터로 한다.
KRX_HOLIDAYS = {
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-03-02", "2026-05-01", "2026-05-05",
    "2026-05-25", "2026-06-03", "2026-08-17", "2026-09-24", "2026-09-25", "2026-10-05", "2026-10-09",
    "2026-12-25", "2026-12-31",
    "2027-01-01", "2027-02-08", "2027-02-09", "2027-03-01", "2027-05-05", "2027-05-13", "2027-08-16",
    "2027-09-14", "2027-09-15", "2027-09-16", "2027-10-04", "2027-10-11", "2027-12-27", "2027-12-31",
}

DEFAULTS = {
    "enabled": False,
    "mode": "approve",  # approve | auto
    "account": "mock",
    "buy_limit_pct": 5.0,
    "strategies": None,
    "migrated": 0,  # 1: 중간값 전략 색을 라임으로 바꿈  # [{id, enabled, per_trade, daily_max, rank, color}]
    # 안전장치 (실투 대비)
    "order_cap": 20_000_000,  # 1회 주문 금액 상한 (원) — 넘으면 그 후보는 사지 않음
    "daily_loss_pct": 8.0,  # 하루 손실 한도: 전날 마감 계좌보다 이만큼(%) 줄면 다음 거래일 매수 하루 쉼 (0 = 끔)
    "skip_risky": True,  # 관리종목·거래정지·투자경고/위험·단기과열·정리매매 종목은 후보에서 뺌
}
# 전략 색 (차트·목록에서 같은 색). 상승 빨강·하락 파랑·강조 호박색과 겹치지 않게
COLORS = ["#A99CF5", "#3CC4B4", "#F08A5D", "#7EC8F2", "#E3B341", "#B5E655", "#FF7AB8"]
LIME = "#B5E655"  # 중간값 전략 (차트의 중간값 선과 같은 색)
NEW_STRATEGY = {"enabled": True, "per_trade": 3_000_000, "daily_max": 5, "rank": "value_asc"}
# 1회 금액: fixed = per_trade 원 고정, pct = 계좌(추정예탁자산)의 pct% (max_per_trade 원까지)
SIZE_DEFAULTS = {"size_mode": "fixed", "pct": 10.0, "max_per_trade": 20_000_000}
RANK_LABEL = {"value_asc": "거래대금 작은 순"}
MANUAL = ("직접 매도", "키움에서 직접 매도", "비상 정지")  # 전략 규칙이 아닌 매도 (통계 따로)
RECONCILE_EVERY = 120  # 초: 키움 잔고와 맞춰 보는 간격
APPROVE_UNTIL = dt.time(8, 45)
BUY_AT = dt.time(8, 50)
OPEN = dt.time(9, 0)
LATE_BUY_UNTIL = dt.time(9, 30)
STOP_UNTIL = dt.time(15, 19)
# 기간만료 매도: 14:30 부터 시장가. 키움 모의투자는 시장가도 실제 거래가 일어나는 만큼만 천천히 체결돼서
# (거래가 적은 종목은 수천 주에 20분~1시간 반) 마감 직전에 내면 다 못 판다 → 한 시간 먼저 내고 마감 동시호가까지 걸어 둔다.
# 주문이 안 들어가면 15:29 까지 20초마다 다시 시도. 마감까지 남은 수량은 다음 거래일 장 시작에 이어서 판다.
EXPIRE_AT = dt.time(14, 30)
EXPIRE_AT_REAL = dt.time(15, 20, 30)  # 실제 계좌: 마감 동시호가에서 한 번에 종가로 (백테스트와 같음)
REAL_FEE, REAL_TAX = 0.00015, 0.002  # 실제 계좌 수수료(사고팔 때) · 거래세(팔 때)
EXPIRE_UNTIL = dt.time(15, 29)
CLOSE_AT = dt.time(15, 31)
FINISH_GIVE_UP = dt.time(23, 0)  # 마감 체결 조회가 계속 안 되면 이때부터는 체결 확인 없이 마무리
UNSURE_CHECK = 600  # 초: 결과 모르는 매도가 이만큼 체결도 잔고 변화도 없으면 '확인 필요'
SETTLE_WAIT = 90  # 초: 체결로 보이는데 잔고가 덜 줄었을 때 이만큼 기다린 뒤에야 '확인 필요' (잔고 반영 지연)
CANCEL_WAIT = 120  # 초: 같은 종목 다른 주식이 있을 때 목표가 취소 확인을 이만큼 기다려도 안 되면 '매도 멈춤' 알림
SHORT_WAIT = 180  # 초: 키움 잔고가 앱 기록보다 적게 보일 때(반영 지연) 매도를 미루는 최대 시간 — 지나면 잔고만큼 판다
CHECK_REMIND = (dt.time(8, 50), dt.time(12, 0), dt.time(14, 0))  # '확인 필요'가 남아 있으면 다시 알리는 때
BAL_STALE_OK = 300  # 초: 이번에 잔고를 못 읽으면 이만큼 안에 정상으로 읽은 잔고로 '이 종목은 우리 몫뿐'인지 판단
CHECK_STOP_STEP, CHECK_STOP_MAX = 3.0, 4  # 확인 필요 종목 손절선 아래 알림: 손절선 대비 3%p 더 떨어질 때마다, 하루 최대 4번
RETARGET_EVERY = 300  # 초: 목표가 주문이 빠진 보유 종목에 다시 거는 간격


def is_trading_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in KRX_HOLIDAYS


def next_trading_day(d: dt.date) -> dt.date:
    d = d + dt.timedelta(days=1)
    while not is_trading_day(d):
        d += dt.timedelta(days=1)
    return d


class Auto:
    def __init__(self, store, kw, get_runner: Callable, notify: Callable[[str, str], None],
                 parse_text: Callable, log_print=print):
        self.store, self.kw, self.get_runner, self.notify, self.parse_text = store, kw, get_runner, notify, parse_text
        self.print = log_print
        self.lock = threading.Lock()
        self._expect: dict = {}
        self._last_rec: dt.datetime | None = None
        self._errs: dict[str, dt.datetime] = {}  # 단계 오류 → 마지막으로 기록한 때
        self.retry_wait = 1.5  # 목표가 취소 뒤 매도 다시 시도 간격(초)
        self._miss: dict[int, dt.datetime] = {}  # 포지션 id → 잔고가 모자란 것을 처음 본 때 (5분 안에 다시 보면 직접 매도로 본다)
        self._hold: dict[int, dt.datetime] = {}  # 포지션 id → '체결로 보이는데 잔고가 덜 줄어든 것'을 처음 본 때
        self._odd: dict[str, int] = {}  # '종목:종류' → 잔고 불일치(키움에만 있음·더 많음)를 연속으로 본 횟수
        self._bal: tuple | None = None  # (now, mode, 잔고) — 한 번의 확인 안에서는 잔고를 한 번만 읽는다
        self._retgt: dict[int, dt.datetime] = {}  # 포지션 id → 목표가 주문을 다시 걸어 본 때
        self._sellfail: dict[int, str] = {}  # 포지션 id → 매도 주문이 실패한 날 (그날은 목표가를 다시 걸지 않는다)
        self._checked: set[int] = set()  # 이번 체결 확인에서 닫기 전 잔고 확인을 한 포지션
        self._cancel_ok: set[tuple] = set()  # (날짜, 목표가 주문번호): 취소가 '접수'로 확인됨
        self._cancel_try: dict[tuple, dt.datetime] = {}  # (날짜, 목표가 주문번호) → 마지막으로 취소를 보낸 때
        self._wait: dict[tuple, dt.datetime] = {}  # (포지션 id, 'cancel'|'short', 날짜) → 매도를 처음 미룬 때
        self._touched: set[tuple] = set()  # 지난 체결 확인 뒤 다시 미룬 기록 (안 미룬 것은 다음 체결 확인 끝에 지운다)
        self._bal_ok: tuple | None = None  # (때, mode, 잔고) 마지막으로 정상으로 읽은 잔고
        self._exc: tuple | None = None  # (now, mode, 체결 내역) 한 번의 확인 안에서 같이 쓴다
        self._ooc: tuple | None = None  # (now, mode, 미체결 또는 None)
        self._why: str | None = None  # 마지막으로 매도를 미룬 이유 ('지금 팔기' 오류 문구용)
        db = store.db
        with _lock:
            db.execute("""CREATE TABLE IF NOT EXISTS at_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL, signal_date TEXT NOT NULL,
                rank INTEGER NOT NULL, value REAL, close REAL NOT NULL, limit_price INTEGER NOT NULL, qty INTEGER NOT NULL,
                status TEXT NOT NULL, order_no TEXT, msg TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE (trade_date, code))""")
            db.execute("""CREATE TABLE IF NOT EXISTS at_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER, code TEXT NOT NULL, name TEXT NOT NULL,
                qty INTEGER NOT NULL, entry_date TEXT NOT NULL, entry_price REAL NOT NULL,
                target_price INTEGER NOT NULL, stop_price REAL NOT NULL,
                status TEXT NOT NULL,
                target_order TEXT, target_date TEXT, target_filled INTEGER NOT NULL DEFAULT 0,
                exit_order TEXT, exit_reason TEXT,
                sold_qty INTEGER NOT NULL DEFAULT 0, sold_amount REAL NOT NULL DEFAULT 0,
                exit_date TEXT, exit_price REAL, ret REAL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS at_equity (
                date TEXT PRIMARY KEY, assets REAL NOT NULL, pnl REAL, ts TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS at_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, text TEXT NOT NULL)""")
            # 여러 전략용 칸 (예전 데이터는 첫 전략으로 본다)
            # base_qty·base_avg: 주문 직전 그 종목 키움 잔고(기준값) · unsure: 결과 모르는 주문('exit' 시장가 매도 · 'target' 목표가)
            # base_sold: 기준값을 적을 때까지 판 것으로 아는 수량 · old_target: 취소가 확인 안 된 옛 목표가 주문
            # carry: 다음 거래일 장 시작에 이어서 팔 사유 · check_msg: '확인 필요' 사유
            for table, cols in (("at_plans", {"strategy_id": "INTEGER", "valid_until": "TEXT", "entry_kind": "TEXT",
                                              "base_qty": "INTEGER", "base_avg": "REAL"}),
                                ("at_positions", {"strategy_id": "INTEGER", "hold_days": "INTEGER", "exit_ref": "REAL",
                                                  "unsure": "TEXT", "unsure_qty": "INTEGER", "unsure_at": "TEXT",
                                                  "unsure_seen": "INTEGER", "base_qty": "INTEGER", "base_sold": "INTEGER",
                                                  "old_target": "TEXT", "carry": "TEXT", "check_msg": "TEXT",
                                                  # claim_*: '확인 필요'가 된 체결 (잔고가 따라오면 이대로 스스로 닫는다)
                                                  "claim_qty": "INTEGER", "claim_amount": "REAL", "claim_reason": "TEXT"})):
                have = {r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}
                for c, typ in cols.items():
                    if c not in have:
                        db.execute(f"ALTER TABLE {table} ADD COLUMN {c} {typ}")
            db.commit()

    # ------------------------------------------------------------ 저장
    def _q(self, sql: str, args=()) -> list[dict]:
        with _lock:
            return [dict(r) for r in self.store.db.execute(sql, args).fetchall()]

    def _x(self, sql: str, args=()) -> int:
        with _lock:
            cur = self.store.db.execute(sql, args)
            self.store.db.commit()
            return cur.lastrowid

    @staticmethod
    def _ts(now: dt.datetime) -> str:
        return now.strftime("%Y-%m-%d %H:%M:%S")

    def log(self, now: dt.datetime, text: str) -> None:
        self._x("INSERT INTO at_log (ts, text) VALUES (?, ?)", (self._ts(now), text))
        self._x("DELETE FROM at_log WHERE id <= (SELECT MAX(id) - 1000 FROM at_log)")

    def settings(self) -> dict:
        raw = self.store.get_setting("autotrade", {})
        s = {**DEFAULTS, **raw}
        if s["strategies"] is None:  # 예전(전략 하나) 설정을 옮긴다
            sid = raw.get("strategy_id") or next((x["id"] for x in self.store.list() if "v2b" in x["name"]), None)
            s["strategies"] = ([{"id": sid, "enabled": True, "per_trade": raw.get("per_trade", 5_000_000),
                                 "daily_max": raw.get("daily_max", 10), "rank": raw.get("rank", "value_asc")}]
                               if sid else [])
        for k, x in enumerate(s["strategies"]):
            x.setdefault("color", COLORS[k % len(COLORS)])
            for kk, vv in SIZE_DEFAULTS.items():
                x.setdefault(kk, vv)
        if s["migrated"] < 1:
            for x in s["strategies"]:
                if x["color"] == "#3CC4B4" and self._is_midpoint(x["id"]):
                    x["color"] = LIME
            s["migrated"] = 1
            self._save(s)
        if s["migrated"] < 2:  # 손실 한도 3% → 8% (v2b 는 하루 3% 출렁임이 흔함) · 3% 로 꺼졌던 자동매매 다시 켬
            if float(s.get("daily_loss_pct") or 0) == 3.0:
                s["daily_loss_pct"] = 8.0
            st = self._state()
            if str(st.get("halted_why") or "").startswith("전날 마감"):
                s["enabled"] = True
                self._set_state(halted=None, halted_why=None)
            s["migrated"] = 2
            self._save(s)
        for k in ("strategy_id", "per_trade", "daily_max", "rank"):
            s.pop(k, None)
        return s

    def _save(self, s: dict) -> None:
        self.store.set_setting("autotrade", {k: v for k, v in s.items() if k in DEFAULTS})

    def set_settings(self, **kw) -> dict:
        """전체 켜기·끄기와 주문 방식. (예전 호출 호환: strategy_id·per_trade·daily_max 는 첫 전략에)"""
        s = self.settings()
        for k in ("enabled", "mode", "order_cap", "daily_loss_pct", "skip_risky"):
            if kw.get(k) is not None:
                s[k] = kw[k]
        if s["mode"] not in ("approve", "auto"):
            raise ValueError("mode 는 approve 또는 auto")
        if not 100_000 <= float(s["order_cap"]) <= 1_000_000_000:
            raise ValueError("1회 주문 상한은 10만~10억 원")
        if not 0 <= float(s["daily_loss_pct"]) <= 50:
            raise ValueError("하루 손실 한도는 0~50%")
        if kw.get("enabled"):  # 다시 켜면 '오늘 멈춤' 표시를 지운다
            st = self._state()
            if st.get("halted"):
                self._set_state(halted=None, halted_why=None)
        self._save(s)
        legacy = {k: kw[k] for k in ("per_trade", "daily_max") if kw.get(k) is not None}
        if kw.get("strategy_id"):
            first = s["strategies"][0]["id"] if s["strategies"] else None
            if first != kw["strategy_id"]:
                if first is not None:
                    self.remove_strategy(first)
                self.set_strategy(kw["strategy_id"], **legacy)
                s = self.settings()
                s["strategies"].sort(key=lambda x: x["id"] != kw["strategy_id"])
                self._save(s)
                return self.settings()
        if legacy and s["strategies"]:
            self.set_strategy(s["strategies"][0]["id"], **legacy)
        return self.settings()

    def _is_midpoint(self, sid: int) -> bool:
        st = self.store.get(sid)
        if not st:
            return False
        e = self.parse_text(st["text"]).entry
        return e.type == "limit" and bool(re.fullmatch(r"\(?(시가\+종가|종가\+시가)\)?/2", e.price.replace(" ", "")))

    def set_strategy(self, sid: int, enabled: bool | None = None, per_trade: float | None = None,
                     daily_max: int | None = None, color: str | None = None, size_mode: str | None = None,
                     pct: float | None = None, max_per_trade: float | None = None) -> dict:
        if not self.store.get(sid):
            raise ValueError("전략이 없습니다")
        spec = self.parse_text(self.store.get(sid)["text"])
        if spec.entry.type == "close":
            raise ValueError("'당일종가' 매수 전략은 자동 매매에 쓸 수 없습니다 (다음날시가·지정가만)")
        s = self.settings()
        x = next((x for x in s["strategies"] if x["id"] == sid), None)
        if x is None:
            used = {y["color"] for y in s["strategies"]}
            first = LIME if self._is_midpoint(sid) and LIME not in used else None
            x = {"id": sid, **NEW_STRATEGY, "color": first or next((c for c in COLORS if c not in used), COLORS[0])}
            s["strategies"].append(x)
        if color is not None and not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
            raise ValueError("색은 #RRGGBB 형식")
        for k, v in (("enabled", enabled), ("per_trade", per_trade), ("daily_max", daily_max), ("color", color),
                     ("size_mode", size_mode), ("pct", pct), ("max_per_trade", max_per_trade)):
            if v is not None:
                x[k] = v
        for k, v in SIZE_DEFAULTS.items():
            x.setdefault(k, v)
        if x["per_trade"] < 100_000 or not 1 <= int(x["daily_max"]) <= 30:
            raise ValueError("1회 매수금은 10만원 이상, 하루 최대는 1~30종목")
        if x["size_mode"] not in ("fixed", "pct"):
            raise ValueError("1회 금액 방식은 fixed 또는 pct")
        if not 1 <= float(x["pct"]) <= 50 or x["max_per_trade"] < 100_000:
            raise ValueError("계좌 비율은 1~50%, 1회 최대 금액은 10만원 이상")
        self._save(s)
        if enabled is False:  # 끈 전략의 아직 주문 안 한 후보는 정리 (이미 산 종목은 규칙대로 계속 판다)
            self._x("""UPDATE at_plans SET status = 'expired', msg = '전략을 끔'
                       WHERE strategy_id = ? AND status IN ('proposed', 'approved')""", (sid,))
        return x

    def amount(self, x: dict, equity: float | None) -> float:
        """이 전략의 1회 매수 금액. 비율 방식인데 계좌 금액을 못 읽으면 고정 금액으로."""
        if x.get("size_mode") == "pct" and equity:
            return min(equity * float(x.get("pct", 10)) / 100, float(x.get("max_per_trade", 20_000_000)))
        return float(x["per_trade"])

    def _equity(self, s: dict, now: dt.datetime) -> float | None:
        """계좌 금액 = 키움 추정예탁자산 (현금 + 보유 종목 평가)."""
        try:
            v = self.kw.balance(s["account"]).get("assets")
            return float(v) if v else None
        except Exception as e:  # noqa: BLE001
            self.log(now, f"⚠ 계좌 금액을 못 읽어 고정 금액으로 계산: {e}")
            return None

    def _snap_equity(self, now: dt.datetime, day: str, mode: str) -> None:
        """장 마감 뒤 하루 한 번 계좌 금액(추정예탁자산)을 남긴다 — 누적 그래프용."""
        try:
            b = self._balance(now, mode)
            if b.get("assets"):
                self._x("INSERT OR REPLACE INTO at_equity (date, assets, pnl, ts) VALUES (?, ?, ?, ?)",
                        (day, float(b["assets"]), b.get("pnl"), now.isoformat(timespec="seconds")))
        except Exception as e:  # noqa: BLE001
            self.log(now, f"⚠ 계좌 금액 기록 실패: {e}")

    def daily(self) -> dict:
        """날마다 실현 손익(판 종목들, 실제 계좌 수수료·세금을 뺀 값) + 장 마감 계좌 금액 기록."""
        rows = self._q("""SELECT id, code, name, strategy_id, exit_reason, exit_date, ret, qty, sold_qty, sold_amount,
                          entry_price, entry_date, exit_price, exit_ref FROM at_positions
                          WHERE status = 'closed' AND exit_date IS NOT NULL AND sold_qty > 0 ORDER BY exit_date, id""")
        first = (self.settings()["strategies"] or [{"id": None}])[0]["id"]
        days: dict[str, dict] = {}
        for r in rows:
            buy = float(r["entry_price"]) * int(r["sold_qty"])
            sell = float(r["sold_amount"])
            cost = buy * REAL_FEE + sell * (REAL_FEE + REAL_TAX)  # 실제 계좌 기준 (모의투자 수수료는 더 높음)
            gross = sell - buy
            net = gross - cost
            d = days.setdefault(r["exit_date"], {"date": r["exit_date"], "pnl": 0.0, "gross": 0.0, "cost": 0.0, "items": []})
            d["pnl"] += net
            d["gross"] += gross
            d["cost"] += cost
            d["items"].append({"code": r["code"], "name": r["name"], "strategy_id": r["strategy_id"] or first,
                               "slip": round((r["exit_price"] / r["exit_ref"] - 1) * 100, 2) if r.get("exit_ref") and r.get("exit_price") else None,
                               "reason": r["exit_reason"], "ret": round(net / buy * 100, 2) if buy else r["ret"],
                               "pnl": round(net), "entry_date": r["entry_date"]})
        for d in days.values():
            d["pnl"], d["gross"], d["cost"] = round(d["pnl"]), round(d["gross"]), round(d["cost"])
            d["items"].sort(key=lambda x: x["pnl"], reverse=True)
        eq = self._q("SELECT date, assets FROM at_equity ORDER BY date")
        start = self._q("SELECT MIN(entry_date) d FROM at_positions")[0]["d"]
        # 거래 금액 합계 (수수료·세금 비교용): 산 금액은 아직 들고 있는 것까지, 판 금액은 판 것만
        tot = self._q("""SELECT COALESCE(SUM(entry_price * qty), 0) buy, COALESCE(SUM(sold_amount), 0) sell
                         FROM at_positions WHERE entry_price > 0""")[0]
        slips = [x["slip"] for d in days.values() for x in d["items"] if x.get("slip") is not None]
        return {"days": sorted(days.values(), key=lambda x: x["date"]), "equity": eq, "start": start,
                "slip": {"n": len(slips), "avg": round(sum(slips) / len(slips), 2) if slips else None},
                "buy_amount": round(tot["buy"]), "sell_amount": round(tot["sell"])}

    def remove_strategy(self, sid: int) -> None:
        s = self.settings()
        s["strategies"] = [x for x in s["strategies"] if x["id"] != sid]
        self._save(s)
        self._x("""UPDATE at_plans SET status = 'expired', msg = '전략을 자동 매매에서 뺌'
                   WHERE strategy_id = ? AND status IN ('proposed', 'approved')""", (sid,))

    def rules(self, sid: int | None = None) -> dict:
        """전략의 매수·청산 규칙: 목표 %, 손절 %, 보유 거래일, 매수 방식."""
        if sid is None:
            ss = self.settings()["strategies"]
            sid = ss[0]["id"] if ss else None
        strat = self.store.get(sid) if sid else None
        if not strat:
            return {"target": 10.0, "stop": 7.0, "hold": 5, "entry": "next_open", "valid_days": 1, "limit_expr": None}
        spec = self.parse_text(strat["text"])
        x = spec.exit
        return {"target": x.target_pct, "stop": x.stop_pct, "hold": int(x.max_days), "entry": spec.entry.type,
                "valid_days": int(spec.entry.valid_days or 1), "limit_expr": spec.entry.price}

    def _levels(self, price: float, sid: int | None = None) -> tuple[int, float]:
        r = self.rules(sid)
        tgt = self.kw.round_tick(price * (1 + r["target"] / 100), up=True) if r["target"] is not None else 0
        stop = price * (1 - r["stop"] / 100) if r["stop"] is not None else 0.0
        return tgt, stop

    def _state(self) -> dict:
        return self.store.get_setting("autotrade_state", {})

    def _set_state(self, **kw) -> None:
        st = self._state()
        st.update(kw)
        self.store.set_setting("autotrade_state", st)

    def plans(self, trade_date: str | None = None) -> list[dict]:
        if trade_date:
            return self._q("SELECT * FROM at_plans WHERE trade_date = ? ORDER BY strategy_id, rank", (trade_date,))
        return self._q("SELECT * FROM at_plans ORDER BY trade_date DESC, rank LIMIT 100")

    def live_plans(self, today: str) -> list[dict]:
        """오늘 주문할 수 있는 후보: 승인됐고 유효기간 안 (지정가 전략은 여러 날)."""
        return self._q("""SELECT * FROM at_plans WHERE status = 'approved' AND trade_date <= ?
                          AND COALESCE(valid_until, trade_date) >= ? ORDER BY trade_date, rank""", (today, today))

    def positions(self, status: str | None = None) -> list[dict]:
        if status == "active":
            return self._q("SELECT * FROM at_positions WHERE status != 'closed' ORDER BY entry_date, id")
        return self._q("""SELECT a.*, p.signal_date, p.limit_price FROM at_positions a LEFT JOIN at_plans p ON p.id = a.plan_id
                          ORDER BY (a.status = 'closed'), COALESCE(a.exit_date, a.entry_date) DESC, a.id DESC LIMIT 200""")

    def _upd(self, table: str, rid: int, now: dt.datetime, **kw) -> None:
        sets = ", ".join(f"{k} = ?" for k in kw)
        self._x(f"UPDATE {table} SET {sets}, updated_at = ? WHERE id = ?", (*kw.values(), self._ts(now), rid))

    # ------------------------------------------------------------ 기대치 vs 실제
    COST_PCT = round((REAL_FEE * 2 + REAL_TAX) * 100, 4)  # 매수·매도 수수료 0.015% 씩 + 거래세 0.20% (백테스트와 같은 가정)

    def expectation(self, sid: int, since: str = "2021-01-01") -> dict | None:
        """전략의 백테스트 기대치 (since 이후 신호, 비용 차감, 거래 1건당). 데이터가 바뀔 때만 다시 계산."""
        st = self.store.get(sid)
        if not st:
            return None
        runner = self.get_runner("kr")
        key = (sid, st["text"], str(runner.panel.dates.max()), since)
        if key in self._expect:
            return self._expect[key]
        import numpy as np

        from .engine import event
        from .research2 import _signals, _sim

        spec = self.parse_text(st["text"])
        t = _sim(runner, spec, _signals(runner, spec))
        p = runner.panel
        m = (t.fill_row >= 0) & (t.outcome_c != event.OPEN) & (p.dates[t.sig] >= np.datetime64(since, "D"))
        r = t.ret_c[m]
        r = r[np.isfinite(r)]
        out = None
        if len(r) >= 20:
            out = {"n": int(len(r)), "avg": round(float(r.mean()), 2), "std": round(float(r.std()), 2),
                   "win": round(float((r > 0).mean() * 100), 1), "since": since}
        self._expect = {k: v for k, v in self._expect.items() if k[0] != sid}
        self._expect[key] = out
        return out

    def compare(self) -> list[dict]:
        """전략마다 모의투자 실제(전략대로 청산한 것만) vs 백테스트 기대치, 그리고 지금 건수에서의 정상 범위."""
        import math

        out = []
        s = self.settings()
        first = s["strategies"][0]["id"] if s["strategies"] else None
        closed = self._q("""SELECT strategy_id, ret, exit_reason, exit_date, qty, sold_qty, sold_amount, entry_price
                            FROM at_positions WHERE status = 'closed' AND ret IS NOT NULL ORDER BY exit_date, id""")
        for x in s["strategies"]:
            st = self.store.get(x["id"])
            if not st:
                continue
            mine = [c for c in closed if (c["strategy_id"] or first) == x["id"] and c["exit_reason"] not in MANUAL]
            rets = [c["ret"] - self.COST_PCT for c in mine]  # 실제는 수수료·세금 전이라 백테스트처럼 빼서 비교
            n = len(rets)
            act = None
            if n:
                avg = sum(rets) / n
                # 실현 손익(실제 매수 금액 기준, 비용 차감)으로 본 최대 낙폭, 가장 긴 연속 손실
                cost = [(c["entry_price"] * (c["sold_qty"] or c["qty"])) or x["per_trade"] for c in mine]
                eq, peak, mdd, streak, worst = 0.0, 0.0, 0.0, 0, 0
                for amt, r in zip(cost, rets):
                    eq += amt * r / 100
                    peak = max(peak, eq)
                    mdd = min(mdd, eq - peak)
                    streak = streak + 1 if r <= 0 else 0
                    worst = max(worst, streak)
                act = {"n": n, "avg": round(avg, 2), "win": round(sum(r > 0 for r in rets) / n * 100, 1),
                       "pnl": round(sum(a * r / 100 for a, r in zip(cost, rets))), "mdd": round(mdd), "lose_streak": worst,
                       "first": mine[0]["exit_date"], "last": mine[-1]["exit_date"]}
            try:
                exp = self.expectation(x["id"])
            except Exception as e:  # noqa: BLE001 — 데이터가 없으면 기대치 없이
                exp = None
                self.log(dt.datetime.now(), f"기대치 계산 실패 ({st['name']}): {e}")
            verdict, band, wband = None, None, None
            if exp and act:
                se = exp["std"] / math.sqrt(n)
                band = [round(exp["avg"] - 2 * se, 2), round(exp["avg"] + 2 * se, 2)]
                p = exp["win"] / 100
                ws = math.sqrt(p * (1 - p) / n) * 100
                wband = [round(max(0, exp["win"] - 2 * ws), 1), round(min(100, exp["win"] + 2 * ws), 1)]
                if n < 30:
                    verdict = "early"
                elif act["avg"] > band[1]:
                    verdict = "above"
                elif act["avg"] < band[0]:
                    verdict = "below"
                else:
                    verdict = "inside"
            out.append({"strategy_id": x["id"], "name": st["name"], "color": x["color"], "per_trade": x["per_trade"],
                        "actual": act, "expect": exp, "band": band, "win_band": wband, "verdict": verdict})
        return out

    # ------------------------------------------------------------ 화면용
    def view(self, today: str | None = None) -> dict:
        s = self.settings()
        first = s["strategies"][0]["id"] if s["strategies"] else None
        latest = self._q("SELECT MAX(trade_date) d FROM at_plans")[0]["d"]
        lp_date = (self._state().get("last_plan") or {}).get("trade_date")
        if lp_date and (not latest or lp_date > latest):
            latest = lp_date  # 최근 계산에서 후보가 0종목이면 옛날 날짜 목록 대신 '후보 없음'
        today = today or dt.date.today().isoformat()
        closed = self._q("SELECT ret, exit_reason, strategy_id FROM at_positions WHERE status = 'closed' AND ret IS NOT NULL")

        def stat(rows):
            r = [c["ret"] for c in rows]
            return {"n": len(r), "win": sum(1 for x in r if x > 0), "avg": round(sum(r) / len(r), 2) if r else None}

        rule = [c for c in closed if c["exit_reason"] not in MANUAL]
        manual = [c for c in closed if c["exit_reason"] in MANUAL]
        strategies = []
        for x in s["strategies"]:
            st = self.store.get(x["id"])
            if not st:
                continue
            r = self.rules(x["id"])
            sid_of = lambda c: c["strategy_id"] or first  # noqa: E731 — 예전 거래는 첫 전략
            strategies.append({**x, "name": st["name"], "rules": r,
                               "midpoint": r["entry"] == "limit" and bool(r["limit_expr"]) and "시가" in r["limit_expr"] and "종가" in r["limit_expr"]
                               and "/ 2" in r["limit_expr"].replace("/2", "/ 2"),
                               "spec": self.parse_text(st["text"]).model_dump(mode="json"),
                               "stats": stat([c for c in rule if sid_of(c) == x["id"]])})
        plans = self._q("""SELECT * FROM at_plans WHERE trade_date = ? OR (status IN ('approved','ordered','partial','unknown','check')
                           AND COALESCE(valid_until, trade_date) >= ?) ORDER BY trade_date DESC, rank""",
                        (latest or "", today))
        for p in plans:
            p["strategy_id"] = p["strategy_id"] or first
        positions = self.positions()
        td = dt.date.fromisoformat(today)
        for p in positions:
            p["strategy_id"] = p["strategy_id"] or first
            if p.get("status") != "closed" and p.get("entry_date"):  # 화면의 'n일째' = 서버 기간만료 셈 그대로 (공휴일 반영)
                try:
                    p["held"] = self._held_days(p["entry_date"], td)
                except Exception:  # noqa: BLE001
                    p["held"] = None
        return {
            "key_saved": bool(self.kw.view().get("mock", {}).get("saved")), "rules": self.rules(),
            "settings": s, "strategies": strategies, "rank_label": RANK_LABEL["value_asc"],
            "strategy": {"id": strategies[0]["id"], "name": strategies[0]["name"]} if strategies else None,
            "spec": strategies[0]["spec"] if strategies else None,
            "plans": plans,
            "plan_date": latest,
            "positions": positions,
            "stats": stat(rule), "stats_manual": stat(manual),
            "last_plan": self._state().get("last_plan"),
            "log": self._q("SELECT ts, text FROM at_log ORDER BY id DESC LIMIT 150"),
            # 키움 잔고에만 있거나 앱 기록보다 많은 종목 (규칙 없이 방치되는 주식) — 잔고 확인 때 갱신
            "untracked": self._state().get("untracked") or [],
            "untracked_at": self._state().get("untracked_at"),
            "state": self._state(),
        }

    def approve(self, pid: int, ok: bool, now: dt.datetime) -> None:
        p = self._q("SELECT * FROM at_plans WHERE id = ?", (pid,))
        if not p:
            raise ValueError("후보가 없습니다")
        p = p[0]
        deadline = dt.datetime.combine(dt.date.fromisoformat(p["trade_date"]), APPROVE_UNTIL)
        if p["status"] not in ("proposed", "approved", "rejected"):
            raise ValueError("이미 주문 단계입니다")
        if now >= deadline and (ok or p["status"] != "approved"):
            raise ValueError("승인 시간(08:45)이 지났습니다")
        # 여러 날 거는 지정가 후보는 둘째 날부터도 '빼기'로 다음 주문을 멈출 수 있다
        self._upd("at_plans", pid, now, status="approved" if ok else "rejected")

    def approve_all(self, trade_date: str, now: dt.datetime) -> int:
        n = 0
        for p in self.plans(trade_date):
            if p["status"] == "proposed":
                self.approve(p["id"], True, now)
                n += 1
        return n

    # ------------------------------------------------------------ 매수 후보
    @staticmethod
    def _data_stamp() -> str:
        """국내 시세·지수 파일이 바뀌었는지 (저녁 임시 → 아침 공식으로 바뀌면 후보를 다시 본다)."""
        from . import config
        from .data import indices

        p = config.dataset_dir("kr") / "prices.parquet"
        try:
            m = p.stat().st_mtime
        except OSError:
            m = 0.0
        return f"{m:.0f}|{indices.mtime('kr'):.0f}"

    @staticmethod
    def _index_note(runner, as_of: str) -> str:
        """지수가 데이터 날짜보다 늦으면 기록에 남긴다 (v2b 의 '시장 하락' 조건이 옛 지수로 계산됨)."""
        try:
            from .data import indices

            late = []
            for key in ("KOSPI", "KOSDAQ"):
                x = indices.load(key)
                if x is not None and len(x) and str(x.index[-1].date()) < as_of:
                    late.append(f"{key} {x.index[-1].month}/{x.index[-1].day}")
            return f" · ⚠ 지수가 옛날 값: {', '.join(late)}" if late else ""
        except Exception:  # noqa: BLE001
            return ""

    def make_plans(self, now: dt.datetime, force: bool = False, only: set[int] | None = None) -> list[dict]:
        """다음 거래일 매수 후보. only 를 주면 그 전략만 (전략을 막 추가했을 때)."""
        s = self.settings()
        strats = [x for x in s["strategies"] if x.get("enabled", True) and self.store.get(x["id"])
                  and (only is None or x["id"] in only)]
        if not strats:
            raise ValueError("자동 매매에 쓸 전략을 고르세요")
        runner = self.get_runner("kr")
        as_of = str(runner.panel.dates.max())
        t = next_trading_day(dt.date.fromisoformat(as_of))
        if now >= dt.datetime.combine(t, APPROVE_UNTIL):
            if force:
                raise ValueError(f"지금 데이터({as_of[5:7]}/{as_of[8:]})로는 {t.month}/{t.day} 매수 후보인데 승인 시간(08:45)이 지났어요. "
                                 "장 마감 뒤 데이터가 들어오면(15:50쯤) 다음 날 후보가 자동으로 만들어져요")
            return []  # 이미 늦음
        status = "approved" if s["mode"] == "auto" else "proposed"
        again = only is None and (self._state().get("last_plan") or {}).get("trade_date") == t.isoformat()
        ix = self._index_note(runner, as_of)
        taken = {p["code"] for p in self.positions("active")} | {
            p["code"] for p in self._q("""SELECT code FROM at_plans WHERE (status IN ('proposed','approved','ordered','partial','unknown')
                                          AND COALESCE(valid_until, trade_date) >= ?) OR (status = 'check' AND trade_date >= ?)""",
                                       (t.isoformat(), (t - dt.timedelta(days=7)).isoformat()))}
        out, per = [], {}
        equity = self._equity(s, now) if any(x.get("size_mode") == "pct" for x in strats) else None
        for x in strats:
            strat = self.store.get(x["id"])
            amount = self.amount(x, equity)
            spec = self.parse_text(strat["text"])
            r_ = self.rules(x["id"])
            if r_["entry"] == "limit":
                # 지정가는 유효기간 동안 주문이 살아 있다: 며칠 전 신호라도 아직 안 닿았으면(남은 날 > 0) 후보
                rows = [r for r in runner.scan(spec)["rows"] if r.get("kind") == "pending" and (r.get("left") or 0) > 0
                        and r.get("limit")]
            else:
                rows = [r for r in runner.scan(spec, 1)["rows"] if r["signal_date"] == as_of]
            rows = [r for r in rows if r["code"] not in taken and r.get("close")
                    and not self._q("SELECT id FROM at_plans WHERE code = ? AND signal_date = ?", (r["code"], r["signal_date"]))]
            rows.sort(key=lambda r: (r.get("value") is None, r.get("value") or 0))
            rows = rows[: int(x["daily_max"])]
            n = 0
            for k, r in enumerate(rows, 1):
                valid_until = t
                for _ in range(int(r.get("left") or 1) - 1 if r_["entry"] == "limit" else 0):
                    valid_until = next_trading_day(valid_until)
                close = float(r["close"])
                if r_["entry"] == "limit":
                    limit = self.kw.round_tick(float(r["limit"]), up=False)
                else:
                    limit = self.kw.round_tick(close * (1 + s["buy_limit_pct"] / 100), up=False)
                qty = int(amount // (limit if r_["entry"] == "limit" else close))
                st, msg = (status, "") if qty >= 1 else ("skipped", "1주 가격이 1회 매수금보다 큼")
                risk = self._risk(r["code"], now) if s.get("skip_risky", True) and st == status else None
                if risk:
                    st, msg = "skipped", f"위험 종목 제외: {risk}"
                if self._q("SELECT id FROM at_plans WHERE trade_date = ? AND code = ?", (t.isoformat(), r["code"])):
                    continue
                self._x("""INSERT INTO at_plans (trade_date, code, name, signal_date, rank, value, close, limit_price,
                           qty, status, msg, created_at, updated_at, strategy_id, valid_until, entry_kind)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (t.isoformat(), r["code"], r["name"], r["signal_date"], k, r.get("value"), close, limit, qty, st, msg,
                         self._ts(now), self._ts(now), x["id"], valid_until.isoformat(), r_["entry"]))
                taken.add(r["code"])
                out.append({**r, "strategy": strat["name"]})
                n += 1
            per[strat["name"]] = n
            if again and n:
                self.log(now, f"{t.month}/{t.day} 매수 후보 {n}종목 추가 — 데이터가 바뀌어 다시 봄 ({strat['name']}, 데이터 {as_of}{ix})")
            elif not again:
                self.log(now, f"{t.month}/{t.day} 매수 후보 {n}종목 ({strat['name']}, 데이터 {as_of}{ix})")
        n = len(out)
        lp = self._state().get("last_plan") or {}
        if only is None and not again:
            self._set_state(planned_asof=as_of, last_plan={"trade_date": t.isoformat(), "signal_date": as_of, "count": n,
                                                           "per": per})
        elif only is None:  # 다시 본 몫만 더한다
            old_per = lp.get("per", {}) if lp.get("trade_date") == t.isoformat() else {}
            self._set_state(planned_asof=as_of, last_plan={
                "trade_date": t.isoformat(), "signal_date": as_of,
                "count": (lp.get("count", 0) if lp.get("trade_date") == t.isoformat() else 0) + n,
                "per": {k: old_per.get(k, 0) + per.get(k, 0) for k in {**old_per, **per}}})
        else:  # 추가한 전략 몫만 더한다
            lp = self._state().get("last_plan") or {}
            if lp.get("trade_date") == t.isoformat():
                self._set_state(last_plan={**lp, "count": lp.get("count", 0) + n, "per": {**lp.get("per", {}), **per}})
            else:
                self._set_state(last_plan={"trade_date": t.isoformat(), "signal_date": as_of, "count": n, "per": per})
        md = f"{t.month}/{t.day}"
        if n:
            names = ", ".join(r["name"] for r in out[:4]) + (f" 외 {n - 4}" if n > 4 else "")
            split = " · ".join(f"{k} {v}" for k, v in per.items() if v)
            if s["mode"] == "approve":
                self.notify(f"{md} 매수 후보 {n}종목 · 승인 필요", f"{split}\n{names}\n08:45 까지 앱(매매 → 키움 자동)에서 승인하세요")
            else:
                self.notify(f"{md} 자동 매수 예정 {n}종목", f"{split}\n{names}\n08:50 에 주문합니다")
        return out

    # ------------------------------------------------------------ 주문
    # 원칙: '결과 모름'(KiwoomUncertain)은 '보냈을 가능성이 큼'으로 보고 절대 자동으로 다시 보내지 않는다.
    # 주문을 낼 때 그 종목의 키움 잔고를 기준값(base_qty)으로 적어 두고, 나중에 체결 내역·잔고 변화로 판단한다.
    # 판단이 안 되면 닫지도 다시 팔지도 않고 '확인 필요'로 두고 알린다. 같은 종목 개인 보유분은 절대 팔거나 가져오지 않는다.
    def _send(self, now: dt.datetime, fn, *args) -> str:
        """주문 보내기. 받은 주문번호는 '오늘 낸 주문'으로 기억한다 (결과 모르는 주문을 체결 내역에서 찾을 때 헷갈리지 않게)."""
        no = str(fn(*args))
        try:
            day = now.date().isoformat()
            sent = self._state().get("sent") or {}
            nos = sent.get("nos", []) if sent.get("date") == day else []
            self._set_state(sent={"date": day, "nos": (nos + [no])[-500:]})
        except Exception:  # noqa: BLE001 — 기억 못 해도 주문은 들어갔다
            pass
        return no

    def _warn(self, now: dt.datetime, key: str) -> bool:
        """같은 일로 하루 한 번만 알리기 (서버를 다시 켜도 기억). 처음이면 True."""
        today = now.date().isoformat()
        w = {k: v for k, v in (self._state().get("warned") or {}).items() if v == today}
        if w.get(key) == today:
            return False
        w[key] = today
        self._set_state(warned=w)
        return True

    def _need_check(self, now: dt.datetime, pos: dict, why: str, **kw) -> None:
        """판단이 안 되는 포지션: 닫지도 팔지도 않고 '확인 필요'로 둔다. 알림은 같은 종목 하루 한 번."""
        self._upd("at_positions", pos["id"], now, status="check", check_msg=why[:200], **kw)
        self.log(now, f"⚠ 확인 필요 · {pos['name']}: {why}")
        if self._warn(now, f"check:{pos['code']}"):
            self.notify(f"⚠ 확인 필요 · {pos['name']}",
                        f"{why}\n키움 앱에서 주문·잔고를 확인한 뒤 앱에서 '다시 관리' 또는 '정리됨'을 눌러 주세요 (그전까지 자동 매매가 손대지 않음)")

    def _place_buys(self, now: dt.datetime, today: str, mode: str) -> None:
        for p in self.plans(today):
            if p["status"] == "proposed":
                self._upd("at_plans", p["id"], now, status="expired", msg="승인하지 않아 사지 않음")
        order = {x["id"]: k for k, x in enumerate(self.settings()["strategies"])}
        on = {x["id"] for x in self.settings()["strategies"] if x.get("enabled", True)}
        plans = [p for p in self.live_plans(today) if (p["strategy_id"] or next(iter(order), None)) in on or not order]
        plans.sort(key=lambda p: (order.get(p["strategy_id"], 0), p["trade_date"], p["rank"]))
        if not plans:
            return
        cap = float(self.settings().get("order_cap") or 0)
        orderable = (self.kw.cash(mode).get("orderable") or 0)
        try:  # 주문 전 잔고 = 기준값 (결과 모르는 매수를 마감에 판정할 때 이보다 늘어난 만큼만 받는다)
            held = self._held(now, mode)
        except Exception as e:  # noqa: BLE001
            held = None
            self.log(now, f"⚠ 매수 전 잔고를 못 읽음 (결과 모르는 주문은 잔고로 판정하지 않고 알리기만): {e}")
        placed, unsure = [], []
        for p in plans:
            need = p["qty"] * p["limit_price"] * 1.001
            if cap and need > cap * 1.06:  # 지정가(+5%) 여유는 봐줌
                self._upd("at_plans", p["id"], now, status="skipped", msg=f"1회 주문 상한({cap / 1e4:,.0f}만) 초과")
                continue
            if orderable < need:
                if today < (p["valid_until"] or p["trade_date"]):
                    self._upd("at_plans", p["id"], now, msg="주문가능금액 부족 · 내일 다시 시도")
                else:
                    self._upd("at_plans", p["id"], now, status="skipped", msg="주문가능금액 부족")
                continue
            h = (held or {}).get(p["code"]) or {}
            base = {"base_qty": int(h.get("qty") or 0) if held is not None else None,
                    "base_avg": float(h.get("avg_price") or 0) if held is not None else None}
            # 보내기 전에 '확인 대기'로 적어 둔다: 보내는 중에 멈춰도 다시 주문하지 않고 체결 내역으로 확인한다
            self._upd("at_plans", p["id"], now, status="unknown", msg="주문 보내는 중", **base)
            try:
                no = self._send(now, self.kw.buy_limit, mode, p["code"], p["qty"], p["limit_price"])
            except KiwoomUncertain as e:
                orderable -= need  # 들어갔을 수 있으니 돈도 쓴 것으로 본다
                self._upd("at_plans", p["id"], now, msg=f"주문 결과 모름 · 체결 내역으로 확인 중: {e}"[:200])
                self.log(now, f"⚠ 매수 주문 결과 모름 {p['name']}: {e} — 다시 내지 않고 체결 내역·잔고로 확인합니다")
                unsure.append(p["name"])
                continue
            except Exception as e:  # noqa: BLE001
                self._upd("at_plans", p["id"], now, status="failed", msg=str(e)[:200])
                self.log(now, f"매수 주문 실패 {p['name']}: {e}")
                continue
            orderable -= need
            self._upd("at_plans", p["id"], now, status="ordered", order_no=no, msg="")
            placed.append(f"{p['name']} {p['qty']}주" + (f" @{p['limit_price']:,}" if p["entry_kind"] == "limit" else ""))
        if placed:
            self.log(now, "매수 주문: " + ", ".join(placed))
            self.notify(f"매수 주문 {len(placed)}건", ", ".join(placed[:6]) + " · 지정가")
        if unsure:
            self.notify(f"⚠ 매수 주문 결과 확인 중 {len(unsure)}건",
                        ", ".join(unsure[:6]) + " · 키움 응답이 없어 다시 내지 않고 체결 내역으로 확인합니다")

    def _place_target(self, now: dt.datetime, pos: dict, mode: str, today: str, after_cancel: bool = False) -> None:
        """목표가 지정가 매도. after_cancel: 방금 목표가 주문을 취소했으면 키움이 처리할 때까지 '매도가능수량 부족'이 나서 잠깐 기다렸다 다시.
        걸 때 그 종목 잔고를 기준값으로 적는다 (체결로 보일 때 잔고가 정말 줄었는지 보려고)."""
        left = pos["qty"] - pos["sold_qty"]
        if left <= 0 or not pos["target_price"] or pos.get("old_target"):
            return
        held = self._qty(now, mode, pos["code"])
        # 막 산 주식이 잔고에 늦게 보일 수 있어 우리 몫보다 작게 적지 않는다
        base = {"base_qty": None if held is None else max(held, left + self._others(pos, today)), "base_sold": pos["sold_qty"]}
        for k in range(4 if after_cancel else 1):
            try:
                no = self._send(now, self.kw.sell_limit, mode, pos["code"], left, pos["target_price"])
                self._upd("at_positions", pos["id"], now, target_order=no, target_date=today, target_filled=0, **base)
                return
            except KiwoomUncertain as e:  # 다시 내면 두 번 걸릴 수 있다 → 체결 내역·잔고로 확인
                self._upd("at_positions", pos["id"], now, target_order=None, target_date=today, target_filled=0,
                          unsure="target", unsure_qty=left, unsure_at=now.isoformat(timespec="seconds"), unsure_seen=0, **base)
                self.log(now, f"⚠ 목표가 주문 결과 모름 {pos['name']}: {e} — 다시 내지 않고 체결 내역·잔고로 확인합니다")
                return
            except Exception as e:  # noqa: BLE001
                if after_cancel and "매도가능수량" in str(e) and k < 3:
                    time.sleep(self.retry_wait)
                    continue
                if self._once(now, f"target:{pos['id']}", every=RETARGET_EVERY):
                    self.log(now, f"목표가 주문 실패 {pos['name']}: {e}")
                return

    @staticmethod
    def _free(pos: dict) -> bool:
        """자동으로 새 주문을 걸어도 되는 보유 종목 (결과 모르는 주문·취소 확인 전 목표가·이어서 팔 것이 없음)."""
        return pos["status"] == "open" and not pos.get("unsure") and not pos.get("old_target") and not pos.get("carry")

    def _refresh_targets(self, now: dt.datetime, today: str, mode: str) -> None:
        for pos in self.positions("active"):
            if self._free(pos) and pos["target_date"] != today:
                self._place_target(now, pos, mode, today)

    def _ensure_targets(self, now: dt.datetime, mode: str, today: str) -> None:
        """장중에 목표가 주문이 빠진 보유 종목 (아침 주문 실패·취소 경합 등) → 5분에 한 번 다시 건다.
        기간만료 매도 시각부터는 하지 않고, 오늘 매도가 실패한 종목도 건드리지 않는다 (매도와 목표가 주문이 번갈아 나가지 않게)."""
        if not (OPEN <= now.time() < EXPIRE_AT):
            return
        for pos in self.positions("active"):
            if not self._free(pos) or not pos["target_price"] or pos["target_date"] == today:
                continue
            if self._sellfail.get(pos["id"]) == today:
                continue
            last = self._retgt.get(pos["id"])
            if last and (now - last).total_seconds() < RETARGET_EVERY:
                continue
            self._retgt[pos["id"]] = now
            self._place_target(now, pos, mode, today)

    def _cancel(self, now: dt.datetime, mode: str, pos: dict, no: str) -> bool | None:
        """목표가 주문 취소. True: 취소 접수 · None: 결과 모름(연결 실패 포함 — 주문이 살아 있을 수 있음)
        · False: 키움이 거절 (이미 체결·취소된 주문일 때)."""
        try:
            self._send(now, self.kw.cancel, mode, pos["code"], no)
            return True
        except KiwoomUncertain as e:
            if self._once(now, f"cancel:{pos['id']}"):
                self.log(now, f"⚠ 목표가 주문 취소 결과 모름 {pos['name']}: {e}")
            return None
        except Exception as e:  # noqa: BLE001
            if self._once(now, f"cancel:{pos['id']}"):
                self.log(now, f"목표가 주문 취소 실패 {pos['name']}: {e}")
            return None if "연결하지 못했습니다" in str(e) else False

    def _stop_buy(self, now: dt.datetime, pos: dict, mode: str) -> None:
        """팔기 시작한 종목에 아직 안 체결된 매수 주문이 남아 있으면 (부분 체결) 그 나머지를 취소한다."""
        if not pos.get("plan_id"):
            return
        p = self._q("SELECT * FROM at_plans WHERE id = ? AND status = 'partial'", (pos["plan_id"],))
        if not p or not p[0]["order_no"] or "남은 매수 주문 취소" in (p[0]["msg"] or ""):
            return
        p = p[0]
        try:
            self._send(now, self.kw.cancel, mode, p["code"], p["order_no"])
        except Exception as e:  # noqa: BLE001
            if self._once(now, f"cancel-buy:{p['id']}"):
                self.log(now, f"⚠ 남은 매수 주문 취소 실패 {p['name']}: {e}")
            return
        # 상태는 'partial' 그대로: 취소 처리 전에 더 체결된 몫도 체결 확인에서 받는다 (마감에 '체결'로)
        self._upd("at_plans", p["id"], now, msg="일부만 체결 · 팔기 시작해 남은 매수 주문 취소")
        self.log(now, f"{p['name']} 남은 매수 주문 취소 (팔기 시작)")

    def _finished(self, mode: str, no: str, code: str, now: dt.datetime | None = None) -> tuple[bool, int | None]:
        """주문이 끝났는지(취소·전부 체결) 와 지금까지 체결 수량. 근거는 둘뿐:
        (i) 미체결 조회(ka10075)가 정상 해석됐는데 그 원주문이 없음, (ii) 체결 내역(ka10076)에 그 원주문이 '미체결 0'.
        미체결을 먼저 읽고 체결 내역을 나중에 읽는다 (그사이 끝난 주문의 체결 수량을 놓치지 않게).
        확인이 안 되면 (False, 체결 수량 또는 None).
        now 를 주면 이번 확인에서 이미 읽은 결과를 같이 쓴다 (보류 종목이 여럿이어도 계좌 조회는 한 번씩) —
        방금 취소를 보낸 직후처럼 새 결과가 필요하면 now 없이 부른다."""
        live = self._live_orders(mode, now)
        if now is not None:
            e = self._ex_for(self._ex_cached(now, mode), no, code, "sell")
        else:
            try:
                e = self._ex_for(self.kw.executions(mode), no, code, "sell")
            except Exception:  # noqa: BLE001
                e = None
        f = int(e["qty"]) if e else None
        if e and e["left"] == 0:
            return True, f
        if live is not None and no not in live:
            return True, f
        return False, f

    def _sell_all(self, now: dt.datetime, pos: dict, mode: str, reason: str, ref: float | None = None) -> bool:
        """목표가 주문 취소 → 시장가 매도. 주문이 들어갔으면(또는 결과 모름이면) True.
        목표가 주문은 취소가 확인될 때까지 old_target 으로 계속 따라가 그 뒤 체결도 반영한다.
        같은 종목 다른 주식이 키움에 있으면(키움이 매도가능수량으로 막아 주지 못함) 목표가 주문이 살아 있을 수 있을 때 팔지 않는다."""
        today = now.date().isoformat()
        name, code = pos["name"], pos["code"]
        self._stop_buy(now, pos, mode)
        live = bool(pos["target_order"]) and pos["target_date"] == today
        tno = pos["target_order"] if live else pos.get("old_target")
        hidden = False  # 결과 모르는 목표가 주문을 찾지 못함 (걸려 있을 수 있음)
        if not tno and pos.get("unsure") == "target":
            tno = self._match(self._seen(mode, self._ex_cached(now, mode), now), code, "sell", pos["unsure_qty"] or 0,
                              self._known_orders(today))
            hidden = not tno
        # old_target 의 취소가 '접수'로 확인됐으면 다시 보내지 않는다 (확인만).
        # 취소가 안 나갔거나(연결 실패) 결과를 모르면 1분에 한 번 다시 보낸다 — 두 번 가도 키움이 거절할 뿐이고,
        # 다시 안 보내면 목표가가 살아 있는 채로 손절·기간만료가 하루 종일 막힌다.
        key = (today, tno)
        last_try = self._cancel_try.get(key)
        fresh_cancel = bool(tno) and not (
            pos.get("old_target") and tno == pos["old_target"]
            and (key in self._cancel_ok or (last_try is not None and (now - last_try).total_seconds() < 60)))
        cancel = self._cancel(now, mode, pos, tno) if fresh_cancel else (True if tno else None)
        if fresh_cancel:
            self._cancel_try[key] = now
            if cancel:
                self._cancel_ok.add(key)
        tf, done = self._tf(pos, today), not tno
        if tno:
            if fresh_cancel and cancel:
                time.sleep(self.retry_wait)  # 취소가 처리될 틈 (처음 한 번만)
            done, f = self._finished(mode, tno, code, None if fresh_cancel else now)
            tf = max(tf, f or 0)

        def mine() -> int:
            return pos["qty"] - pos["sold_qty"] - tf

        def track() -> dict:  # 목표가 주문 정리: 끝난 게 확인됐으면 판 수량에 합치고, 아니면 old_target 으로 계속 따라간다
            if not tno:
                return {"target_order": None}
            if done:
                return {"target_order": None, "old_target": None, "target_filled": 0, "sold_qty": pos["sold_qty"] + tf,
                        "sold_amount": pos["sold_amount"] + tf * pos["target_price"]}
            return {"target_order": None, "old_target": tno, "target_filled": tf}

        if mine() <= 0:  # 목표가로 다 팔렸다 → 체결 확인이 '목표'로 닫는다
            upd = {"target_filled": tf}
            if not live and not pos.get("old_target"):  # 결과 모르던 목표가 주문을 찾았다 → 그 주문으로
                upd.update(target_order=tno, target_date=today, unsure=None, unsure_qty=None, unsure_at=None)
            self._upd("at_positions", pos["id"], now, **upd)
            return True
        others = self._others(pos, today)
        held, stale = self._qty_recent(now, mode, code)  # 이번 확인의 잔고 (못 읽으면 5분 안의 정상 잔고)

        def gate(held: int | None) -> tuple[str | None, bool]:
            """팔지 미룰 이유와 바로 알릴지. 이 종목이 우리 몫뿐이면(키움이 두 번 팔기를 막아 줌) 목표가 취소 확인 없이 판다."""
            excl = held is not None and others == 0 and held <= mine()
            cause = ("잔고를 못 읽어" if held is None else "같은 종목 다른 포지션이 있어" if others
                     else f"앱 기록에 없는 같은 종목 주식(개인분 {held - mine()}주)이 있어")
            if not excl and hidden:
                return f"결과 모르는 목표가 주문이 걸려 있을 수 있는데 {cause}", self._warn(now, f"block:{pos['id']}")
            if not excl and tno and not done:
                first = self._touch((pos["id"], "cancel", today), now)
                alert = (now - first).total_seconds() >= CANCEL_WAIT and self._warn(now, f"cancel-wait:{pos['id']}")
                return f"목표가 주문 취소가 아직 확인되지 않았고 {cause}", alert
            if held is not None and held - others < mine():
                first = self._touch((pos["id"], "short", today), now)
                if held - others <= 0:
                    return "키움 잔고에 팔 주식이 아직 안 보여", False
                if (now - first).total_seconds() < SHORT_WAIT:  # 잔고 반영이 늦을 수 있다 → 일부만 팔지 말고 잠깐 미룬다
                    return f"키움 잔고({held - others}주)가 앱 기록({mine()}주)보다 적어", False
            return None, False

        excl0 = held is not None and others == 0 and held <= mine()
        if tno and not done and not excl0 and not hidden and (pos["id"], "cancel", today) not in self._wait:
            # 처음 보류할 때만 잠깐(1.5초 × 4) 다시 본다. 다음 확인부터는 한 번만 (계좌 조회·시간 아끼기)
            for _ in range(4):
                time.sleep(self.retry_wait)
                done, f = self._finished(mode, tno, code)
                tf = max(tf, f or 0)
                if done:
                    break
            if mine() <= 0:  # 기다리는 사이 목표가로 다 팔렸다 → 체결 확인이 '목표'로 닫는다
                self._upd("at_positions", pos["id"], now, target_filled=tf)
                return True
        why, alert = gate(held)
        fresh = None
        if not why:  # 내기 직전 잔고를 새로 읽어 기준값으로 (체결 내역을 먼저 읽고 잔고를 나중에 — 기준값이 커지지 않게)
            fresh = self._qty(now, mode, code, fresh=True)
            if fresh is not None:
                held, stale = fresh, False
                why, alert = gate(held)
        if why:
            self._why = why
            self._upd("at_positions", pos["id"], now, **track())
            if reason not in MANUAL:
                self._sellfail[pos["id"]] = today
            if self._once(now, f"defer:{pos['id']}"):
                self.log(now, f"⚠ {reason} 매도 미룸 {name}: {why} 팔지 않았습니다")
            if alert:
                self.log(now, f"⚠ {reason} 매도 멈춤 {name}: {why} — 키움 앱에서 이 종목의 미체결 목표가 주문을 확인·취소해 주세요")
                self.notify(f"⚠ {reason} 매도 멈춤 · {name}",
                            f"{why} 팔지 않았습니다 (다른 주식까지 팔리지 않게). 키움 앱에서 목표가 주문을 확인·취소해 주세요")
            self._set_state(last_error=f"{now.isoformat(timespec='seconds')} {reason} 매도 미룸 {name}: {why}")
            return False
        for k in ("cancel", "short"):
            self._wait.pop((pos["id"], k, today), None)

        def size() -> int:  # 시장가 수량 = 앱 기록 남은 수량, 지금 읽은 키움 잔고(다른 포지션 몫 뺀 것)보다 많게는 안 냄
            return mine() if held is None or stale else min(mine(), max(0, held - others))

        no, err = None, None
        for k in range(4):
            try:
                no = self._send(now, self.kw.sell_market, mode, code, size())
                break
            except KiwoomUncertain as e:  # 다시 내면 두 번 팔릴 수 있다 → 체결 내역·잔고로 확인
                err = e
                break
            except Exception as e:  # noqa: BLE001
                err = e
                # 목표가 주문을 막 취소했으면 키움이 처리하기 전엔 '매도가능수량 부족'이 난다 → 잠깐 기다렸다 다시
                if not (tno and "매도가능수량" in str(e) and k < 3):
                    break
                time.sleep(self.retry_wait)
                done, f = self._finished(mode, tno, code)
                tf = max(tf, f or 0)
                if mine() <= 0:
                    self._upd("at_positions", pos["id"], now, target_filled=tf)
                    return True
                hf = self._qty(now, mode, code, fresh=True)
                if hf is not None:
                    held, stale = hf, False
                if held is not None and not stale and size() <= 0:
                    break
        # 이 매도 직전 잔고와 그때까지 판 것으로 아는 수량 (예전 잔고는 기준값으로 쓰지 않는다 → 체결 내역과 '옛 포지션' 규칙으로 판정)
        base = {"base_qty": None if stale else held, "base_sold": pos["sold_qty"] + tf}
        if no is not None:
            sent = size()
            clear = {"unsure": None, "unsure_qty": None, "unsure_at": None, "unsure_seen": None}
            self._upd("at_positions", pos["id"], now, status="closing", exit_order=no, exit_reason=reason, exit_ref=ref,
                      carry=None, check_msg=None, **clear, **base, **track())
            self.log(now, f"{reason} 매도 주문 {name} {sent}주 (시장가)" + (f" · 기준가 {ref:,.0f}원" if ref else "")
                     + (f" · 목표가로 {tf}주 먼저 팔림" if tf else "") + f" · 주문번호 {no}"
                     + (f" · 키움 잔고가 적어 {mine()}주 중 {sent}주만" if sent < mine() else ""))
            return True
        if isinstance(err, KiwoomUncertain):
            self._upd("at_positions", pos["id"], now, status="closing", exit_order=None, exit_reason=reason, exit_ref=ref,
                      carry=None, unsure="exit", unsure_qty=size(), unsure_at=now.isoformat(timespec="seconds"), unsure_seen=0,
                      **base, **track())
            self.log(now, f"⚠ {reason} 매도 주문 결과 모름 {name} {size()}주: {err} — 다시 내지 않고 체결 내역·잔고로 확인합니다")
            self.notify(f"⚠ {reason} 매도 확인 중 · {name}", "키움 응답이 없어 다시 내지 않고 체결 내역·잔고로 확인합니다")
            return True
        upd = track()
        if tno and reason in MANUAL:  # 직접 누른 매도가 안 됐으면 목표가는 (취소 확인 뒤) 다시 건다
            upd["target_date"] = None
        self._upd("at_positions", pos["id"], now, **upd)
        if reason not in MANUAL:  # 다시 시도하니 그사이 목표가를 다시 걸지 않는다
            self._sellfail[pos["id"]] = today
        blocked = "매도가능수량" in str(err)
        if blocked and self._warn(now, f"block:{pos['id']}"):
            # 숨은 목표가 주문 때문에 막힌 것일 수 있다 → 10분 침묵 없이 처음 한 번은 바로 알린다
            self.log(now, f"⚠ {reason} 매도가 '매도가능수량 부족'으로 막힘 {name} — 키움 앱에서 미체결 목표가 주문을 취소해 주세요")
            self.notify(f"⚠ {reason} 매도 막힘 · {name}",
                        "'매도가능수량 부족'으로 매도가 막혔습니다. 키움 앱에서 이 종목의 미체결 목표가(매도) 주문이 남아 있으면 취소해 주세요")
        elif self._once(now, f"sell:{pos['id']}:{reason}"):
            self.log(now, f"{reason} 매도 주문 실패 {name}: {err}")
            if not blocked:  # 막힘 알림은 그날 이미 보냈다
                self.notify(f"⚠ {reason} 매도 실패", f"{name}: {err}")
        self._set_state(last_error=f"{now.isoformat(timespec='seconds')} {reason} 매도 {name}: {str(err)[:200]}")
        return False

    def _once(self, now: dt.datetime, key: str, every: int = 600) -> bool:
        last = self._errs.get(key)
        if last is not None and (now - last).total_seconds() < every:
            return False
        self._errs[key] = now
        return True

    def manual_sell(self, pid: int, now: dt.datetime) -> None:
        """앱의 '지금 팔기': 목표가 주문 취소 → 시장가 매도. 통계는 '직접 매도'로 따로."""
        with self.lock:
            pos = self._q("SELECT * FROM at_positions WHERE id = ?", (pid,))
            if not pos:
                raise ValueError("보유 종목이 없습니다")
            pos = pos[0]
            if pos["status"] == "check":
                raise ValueError("확인 필요 상태입니다. 키움 앱에서 확인한 뒤 '다시 관리' 또는 '정리됨'을 눌러 주세요")
            if pos["status"] != "open":
                raise ValueError("이미 매도 중이거나 끝난 종목입니다")
            if not is_trading_day(now.date()) or not (dt.time(9, 0) <= now.time() < EXPIRE_UNTIL):
                raise ValueError("장중(09:00~15:29)에만 팔 수 있습니다")
            self._why = None
            if not self._sell_all(now, pos, self.settings()["account"], "직접 매도"):
                if self._why:  # 일부러 미룬 것 (예: 목표가 취소 확인 전) → 이유를 그대로
                    raise ValueError(f"지금은 팔지 않았습니다: {self._why} — 잠시 뒤 다시 눌러 주세요")
                raise ValueError("매도 주문을 내지 못했습니다. 기록을 확인하세요")

    def resolve(self, pid: int, action: str, now: dt.datetime) -> None:
        """'확인 필요' 포지션을 사람이 키움 앱에서 확인한 뒤 정리한다.
        resume: 남은 수량이 키움에 그대로 있고 걸린 주문도 없음 → 다시 규칙대로 관리.
        close: 이미 팔렸거나 직접 정리함 → 끝난 것으로 (남은 수량은 지금 값으로 '키움에서 직접 매도')."""
        with self.lock:
            pos = self._q("SELECT * FROM at_positions WHERE id = ?", (pid,))
            if not pos or pos[0]["status"] != "check":
                raise ValueError("확인 필요 상태인 종목이 아닙니다")
            pos = pos[0]
            clear = {"unsure": None, "unsure_qty": None, "unsure_at": None, "unsure_seen": None, "check_msg": None,
                     "old_target": None, "exit_order": None, "target_order": None, "target_filled": 0, "target_date": None,
                     "base_qty": None, "base_sold": None, "carry": None}
            left = pos["qty"] - pos["sold_qty"]
            clear.update(claim_qty=None, claim_amount=None, claim_reason=None)
            if action == "resume":
                mode = self.settings()["account"]
                for no in {pos.get("old_target"), pos.get("exit_order"), pos.get("target_order")} - {None}:
                    if self._alive(mode, no, pos["code"]):  # 키움에 아직 걸려 있으면 다시 관리하지 않는다 (두 번 팔리지 않게)
                        raise ValueError(f"키움에 이 종목 매도 주문(주문번호 {no})이 아직 걸려 있습니다. 키움 앱에서 취소한 뒤 다시 눌러 주세요")
                self._upd("at_positions", pos["id"], now, status="open", **clear)
                self.log(now, f"확인 후 다시 관리 · {pos['name']} 남은 {left}주")
            elif action == "close":
                self._upd("at_positions", pos["id"], now, **clear)
                try:
                    price = self.kw.quotes(self.settings()["account"], [pos["code"]]).get(pos["code"])
                except Exception:  # noqa: BLE001
                    price = None
                price = price or pos["entry_price"]
                self._close(now, {**pos, **clear}, "키움에서 직접 매도", pos["qty"], pos["sold_amount"] + left * price)
                self.log(now, f"확인 후 정리 · {pos['name']} (남은 {left}주는 {price:,.0f}원으로 기록)")
            else:
                raise ValueError("action 은 resume 또는 close")

    def resolve_plan(self, pid: int, action: str, now: dt.datetime, qty: int | None = None,
                     price: float | None = None) -> None:
        """'확인 필요' 매수 후보를 사람이 키움 앱에서 확인한 뒤 정리한다.
        bought: 실제로 샀음 → 입력한 수량·가격으로 포지션을 연다 (목표가는 장중이면 곧, 아니면 다음 거래일 아침)
        not_bought: 안 샀음 → 실패로."""
        with self.lock:
            p = self._q("SELECT * FROM at_plans WHERE id = ?", (pid,))
            if not p or p[0]["status"] != "check":
                raise ValueError("확인 필요 상태인 매수 후보가 아닙니다")
            p = p[0]
            if action == "not_bought":
                self._upd("at_plans", p["id"], now, status="failed", msg="확인 후: 사지 않음")
                self.log(now, f"확인 후 정리 · {p['name']} 매수 안 됨")
            elif action == "bought":
                if not qty or not 1 <= int(qty) <= int(p["qty"]) or not price or float(price) <= 0:
                    raise ValueError(f"수량(1~{p['qty']}주)과 가격을 확인해 주세요")
                first = (self.settings()["strategies"] or [{"id": None}])[0]["id"]
                self._open_pos(now, self.settings()["account"], p, int(qty), float(price), p["strategy_id"] or first,
                               p["trade_date"], target=False)
                self._upd("at_plans", p["id"], now, status="filled", msg=f"확인 후: {int(qty)}주 {float(price):,.0f}원 매수 기록")
            else:
                raise ValueError("action 은 bought 또는 not_bought")

    def _alive(self, mode: str, no: str, code: str) -> bool:
        """그 주문이 키움에 아직 살아 있다는 근거가 있는지 (미체결 목록에 있음, 또는 체결 내역에 미체결이 남음)."""
        live = self._live_orders(mode)
        if live is not None and no in live:
            return True
        e = self._ex_for(self._ex_or_empty(mode), no, code, "sell")
        return bool(e and e["left"] > 0)

    # ------------------------------------------------------------ 키움 잔고 · 결과 모르는 주문
    def _balance(self, now: dt.datetime, mode: str) -> dict:
        """키움 잔고. 계좌 TR 은 초당 1회 제한이라 한 번의 확인(같은 now) 안에서는 한 번만 읽는다."""
        if self._bal is not None and self._bal[0] == now and self._bal[1] == mode:
            return self._bal[2]
        b = self.kw.balance(mode)
        self._bal = self._bal_ok = (now, mode, b)
        return b

    def _held(self, now: dt.datetime, mode: str) -> dict[str, dict]:
        return {h["code"]: h for h in self._balance(now, mode).get("holdings") or []}

    def _qty(self, now: dt.datetime, mode: str, code: str, fresh: bool = False) -> int | None:
        """키움 잔고의 그 종목 수량. fresh: 주문 직전 기준값용으로 새로 읽는다. 못 읽으면 None."""
        try:
            if fresh:
                b = self.kw.balance(mode)
                self._bal = self._bal_ok = (now, mode, b)
            else:
                b = self._balance(now, mode)
        except Exception as e:  # noqa: BLE001
            if self._once(now, f"bal:{str(e)[:40]}"):
                self.log(now, f"⚠ 잔고 확인 실패: {e}")
            return None
        return int(next((h.get("qty") or 0 for h in b.get("holdings") or [] if h["code"] == code), 0))

    def _qty_recent(self, now: dt.datetime, mode: str, code: str) -> tuple[int | None, bool]:
        """(그 종목 수량, 예전 값인지). 이번 확인에서 못 읽으면 5분 안에 정상으로 읽은 잔고를 쓴다. 그것도 없으면 (None, False)."""
        q = self._qty(now, mode, code)
        if q is not None:
            return q, False
        ok = self._bal_ok
        if ok and ok[1] == mode and 0 <= (now - ok[0]).total_seconds() <= BAL_STALE_OK:
            return int(next((h.get("qty") or 0 for h in ok[2].get("holdings") or [] if h["code"] == code), 0)), True
        return None, False

    def _ex_cached(self, now: dt.datetime, mode: str) -> dict:
        """체결 내역 (한 번의 확인 안에서는 한 번만 읽는다). 못 읽으면 빈 결과."""
        if self._exc is not None and self._exc[0] == now and self._exc[1] == mode:
            return self._exc[2]
        ex = self._ex_or_empty(mode)
        self._exc = (now, mode, ex)
        return ex

    def _touch(self, key: tuple, now: dt.datetime) -> dt.datetime:
        """미룬 기록: 처음 미룬 때를 돌려주고 '이번에도 미룸'으로 표시."""
        self._touched.add(key)
        return self._wait.setdefault(key, now)

    @staticmethod
    def _tf(pos: dict, today: str) -> int:
        """오늘 따라가는 목표가 주문(target_order, 또는 취소 확인 전 old_target)이 판 수량."""
        return pos["target_filled"] if (pos["target_date"] == today or pos.get("old_target")) else 0

    def _remain(self, pos: dict, today: str) -> int:
        """이 포지션 몫으로 키움에 남아 있어야 할 수량 (오늘 목표가로 팔린 것은 뺀다)."""
        return pos["qty"] - pos["sold_qty"] - self._tf(pos, today)

    def _others(self, pos: dict, today: str) -> int:
        """같은 종목 다른 활성 포지션 몫 (많게 잡는다)."""
        return sum(max(0, self._remain(p, today)) for p in self.positions("active")
                   if p["code"] == pos["code"] and p["id"] != pos["id"])

    def _ex_or_empty(self, mode: str) -> dict:
        try:
            return self.kw.executions(mode)
        except Exception:  # noqa: BLE001
            return {}

    @staticmethod
    def _ex_for(ex: dict, no: str | None, code: str, side: str) -> dict | None:
        """주문번호로 체결 내역 찾기. 종목·방향이 다르면(다른 날 주문번호 겹침 등) 없는 것으로, 원주문(kind=order)이 아닌 줄도 뺀다."""
        e = ex.get(no) if no else None
        if not e or e.get("kind") != "order":
            return None
        if (e.get("code") and e["code"] != code) or (e.get("side") and e["side"] != side):
            return None
        return e

    def _known_orders(self, today: str) -> set[str]:
        """오늘 우리가 아는 주문번호 (결과 모르는 주문을 체결 내역에서 찾을 때 이것들은 남의 주문이 아니다)."""
        rows = self._q("""SELECT order_no o FROM at_plans WHERE order_no IS NOT NULL
                          AND (status IN ('ordered', 'partial') OR updated_at >= ?)""", (today,))
        rows += self._q("""SELECT target_order o FROM at_positions WHERE target_order IS NOT NULL AND target_date = ?
                           UNION SELECT old_target o FROM at_positions WHERE old_target IS NOT NULL
                           UNION SELECT exit_order o FROM at_positions WHERE exit_order IS NOT NULL
                           AND (status != 'closed' OR updated_at >= ?)""", (today, today))
        sent = self._state().get("sent") or {}
        return {str(r["o"]) for r in rows} | (set(sent.get("nos", [])) if sent.get("date") == today else set())

    def _live_orders(self, mode: str, now: dt.datetime | None = None) -> dict | None:
        """미체결 주문 (조회가 안 되면 None — '없음'과 구분). now 를 주면 한 번의 확인 안에서 한 번만 읽는다."""
        if now is not None and self._ooc is not None and self._ooc[0] == now and self._ooc[1] == mode:
            return self._ooc[2]
        fn = getattr(self.kw, "open_orders", None)
        live = None
        if fn is not None:
            try:
                live = fn(mode)
            except Exception:  # noqa: BLE001
                live = None
        if now is not None:
            self._ooc = (now, mode, live)
        return live

    def _seen(self, mode: str, ex: dict, now: dt.datetime | None = None) -> dict:
        """결과 모르는 주문을 찾을 주문 목록: 오늘 체결 내역 + 아직 체결 안 된 주문(미체결 조회, 안 되면 빼고)."""
        live = self._live_orders(mode, now)
        return {**live, **ex} if live else ex

    @staticmethod
    def _match(ex: dict, code: str, side: str, qty: int, known: set[str]) -> str | None:
        """체결 내역에서 우리가 모르는 같은 종목·방향·수량 원주문 (결과 모르던 주문이 실제로 들어갔는지). 취소·정정 줄은 뺀다."""
        cands = [no for no, e in ex.items() if no not in known and e.get("kind") == "order"
                 and e.get("code") == code and e.get("side") == side and int(e.get("ordered") or 0) == int(qty)]
        return min(cands, key=lambda n: (len(n), n)) if cands else None

    def _guess_price(self, mode: str, pos: dict, seen: dict, known: set[str]) -> float:
        """체결 내역 없이 잔고로 판 것을 확인했을 때 가격: 우리가 모르는 그 종목 매도 체결 평균 → 현재가 → 매수가."""
        sells = [e for no, e in seen.items() if no not in known and e.get("kind") == "order"
                 and e.get("code") == pos["code"] and e.get("side") == "sell" and e.get("qty")]
        if sells:
            return sum(e["qty"] * e["price"] for e in sells) / sum(e["qty"] for e in sells)
        try:
            v = self.kw.quotes(mode, [pos["code"]]).get(pos["code"])
        except Exception:  # noqa: BLE001
            v = None
        return float(v or pos["entry_price"])

    def _resolve_buys(self, now: dt.datetime, ex: dict, today: str) -> None:
        """결과 모르는 매수 주문: 오늘 체결 내역에 같은 종목·수량의 모르는 매수 주문이 있으면 그 주문으로 본다."""
        known = self._known_orders(today)
        for p in self._q("SELECT * FROM at_plans WHERE status = 'unknown' ORDER BY id"):
            no = self._match(ex, p["code"], "buy", p["qty"], known)
            if no:
                known.add(no)
                self._upd("at_plans", p["id"], now, status="ordered", order_no=no, msg="체결 내역에서 주문 확인")
                self.log(now, f"매수 주문 확인 {p['name']} · 주문번호 {no} (결과 모르던 주문)")

    def _track_old_targets(self, now: dt.datetime, mode: str, ex: dict, today: str) -> None:
        """취소가 확인되지 않은 옛 목표가 주문: 그 뒤 체결도 반영하고, 끝난 게 확인되면 판 수량에 합친다."""
        rows = [p for p in self.positions("active") if p.get("old_target")]
        if not rows:
            return
        live = self._live_orders(mode, now)
        for pos in rows:
            e = self._ex_for(ex, pos["old_target"], pos["code"], "sell")
            tf = max(pos["target_filled"], int(e["qty"]) if e else 0)
            if (e and e["left"] == 0) or (live is not None and pos["old_target"] not in live):
                self._upd("at_positions", pos["id"], now, old_target=None, target_filled=0, sold_qty=pos["sold_qty"] + tf,
                          sold_amount=pos["sold_amount"] + tf * pos["target_price"])
            elif tf != pos["target_filled"]:
                self._upd("at_positions", pos["id"], now, target_filled=tf)
        for pos in self.positions("active"):  # 목표가로 다 팔린 보유 종목 → '목표'로 정리 (잔고 확인 포함)
            if pos["status"] == "open" and pos["qty"] - pos["sold_qty"] - self._tf(pos, today) <= 0:
                tf = self._tf(pos, today)
                self._settle(now, mode, pos, "목표", pos["sold_qty"] + tf, pos["sold_amount"] + tf * pos["target_price"])

    def _resolve_unsure(self, now: dt.datetime, mode: str, seen: dict, today: str, decisive: bool = False) -> None:
        """결과 모르는 매도·목표가 주문을 다시 내지 않고 판단한다.
        1) 체결 내역·미체결에서 같은 종목·수량의 모르는 원주문을 찾으면 그 주문으로.
        2) 못 찾아도 잔고가 기준값에서 줄어든 만큼은 판 것으로.
        3) 매도: 10분 동안 체결도 잔고 변화도 없으면 '확인 필요'. 마감(decisive)에는 잔고로 최종 판정 (주문은 마감에 사라진다)."""
        known = self._known_orders(today)
        for pos in self.positions("active"):
            kind = pos.get("unsure")
            if not kind:
                continue
            clear = {"unsure": None, "unsure_qty": None, "unsure_at": None, "unsure_seen": None, "check_msg": None}
            no = self._match(seen, pos["code"], "sell", pos["unsure_qty"] or 0, known)
            if no:
                known.add(no)
                if kind == "exit":
                    self._upd("at_positions", pos["id"], now, status="closing", exit_order=no, **clear)
                else:
                    self._upd("at_positions", pos["id"], now, target_order=no, target_date=today, target_filled=0, **clear)
                self.log(now, f"{'매도' if kind == 'exit' else '목표가'} 주문 확인 {pos['name']} · 주문번호 {no} (결과 모르던 주문)")
                continue
            tf, tp = self._tf(pos, today), pos["target_price"]
            if pos["qty"] - pos["sold_qty"] - tf <= 0:  # 따라가던 목표가 주문이 다 팔았다
                self._close(now, pos, "목표", pos["sold_qty"] + tf, pos["sold_amount"] + tf * tp)
                continue
            held = self._qty(now, mode, pos["code"])
            base, bsold = pos.get("base_qty"), pos.get("base_sold")
            if held is None or base is None:
                if kind == "exit" and pos["status"] != "check" and (decisive or self._unsure_age(now, pos) >= UNSURE_CHECK):
                    self._need_check(now, pos, "매도 주문 결과를 모르고 " + ("잔고를 못 읽어" if held is None else "주문 전 잔고 기준이 없어")
                                     + " 판단하지 못했습니다")
                continue
            drop = base - held - ((pos["sold_qty"] + tf) - (bsold or 0))  # 이 주문이 판 것으로 보이는 수량
            if kind == "target":
                left = pos["qty"] - pos["sold_qty"]
                if drop >= left:
                    self._close(now, pos, "목표", pos["sold_qty"] + left, pos["sold_amount"] + left * tp)
                elif drop > pos["target_filled"]:
                    self._upd("at_positions", pos["id"], now, target_filled=drop)  # 잔고로 본 목표가 체결
                continue
            want = int(pos["unsure_qty"] or 0)
            if drop >= want:  # 잔고로 다 팔린 게 확인됨
                price = self._guess_price(mode, pos, seen, known)
                self.log(now, f"{pos['name']} 결과 모르던 매도: 체결 내역엔 없지만 잔고가 {want}주 줄어 판 것으로 봅니다 ({price:,.0f}원 추정)")
                self._close(now, pos, pos["exit_reason"], pos["sold_qty"] + tf + want, pos["sold_amount"] + tf * tp + want * price)
                continue
            if decisive:  # 마감: 남은 주문은 사라졌다 → 줄어든 만큼만 판 것으로, 나머지는 다음 거래일 장 시작에 이어서
                d = max(0, drop)
                price = self._guess_price(mode, pos, seen, known) if d else 0
                self._upd("at_positions", pos["id"], now, status="open", exit_order=None, carry=pos["exit_reason"],
                          sold_qty=pos["sold_qty"] + d, sold_amount=pos["sold_amount"] + d * price, **clear)
                self.log(now, f"⚠ {pos['name']} 결과 모르던 {pos['exit_reason']} 매도: 잔고로 {d}주 판 것으로 확인 · "
                              f"남은 {pos['qty'] - pos['sold_qty'] - tf - d}주는 다음 거래일 장 시작에 이어서 팝니다")
                continue
            if drop != (pos.get("unsure_seen") or 0):  # 체결이 진행 중 → 기다린다
                self._upd("at_positions", pos["id"], now, unsure_seen=drop, unsure_at=now.isoformat(timespec="seconds"))
            elif pos["status"] != "check" and self._unsure_age(now, pos) >= UNSURE_CHECK:
                self._need_check(now, pos, f"{pos['exit_reason']} 매도 주문 결과를 모름 · 10분 동안 체결도 잔고 변화도 없음 "
                                           "(키움 앱에서 이 종목 매도 주문이 있는지 확인)")

    @staticmethod
    def _unsure_age(now: dt.datetime, pos: dict) -> float:
        return (now - dt.datetime.fromisoformat(pos["unsure_at"])).total_seconds() if pos.get("unsure_at") else 0.0

    def _settle(self, now: dt.datetime, mode: str, pos: dict, reason: str, total: int, amount: float,
                decisive: bool = False) -> bool:
        """체결로 보이는 포지션을 닫기 전에 키움 잔고를 본다: 이 매도 직전 잔고(기준값)에서 실제로 줄어든 수량이
        체결로 보인 수량보다 적으면 닫지 않고 실제로 줄어든 만큼만 반영해 '확인 필요'로 둔다.
        (10/29: 취소된 목표가 주문을 '전부 체결'로 읽고 닫았는데 키움엔 236주가 남아 규칙 없이 방치됐다)
        잔고 반영이 늦을 수 있어 처음 본 뒤 90초가 지나야 (decisive=True 면 한 번에 — 장 마감 정리).
        '확인 필요'가 되어도 기준값과 체결로 보인 수량(claim)을 남겨, 나중에 잔고가 따라오면 스스로 닫힌다 (_recheck)."""
        self._checked.add(pos["id"])
        held = self._qty(now, mode, pos["code"])
        if held is None:
            if not decisive:
                return False  # 다음 확인 때 다시
            self.log(now, f"⚠ 잔고를 못 읽어 체결 내역만 보고 정리합니다: {pos['name']}")
            self._close(now, pos, reason, total, amount)
            return True
        today = now.date().isoformat()
        base, bsold = pos.get("base_qty"), pos.get("base_sold")
        if base is None:  # 기준값이 없는 옛 포지션: 이 종목 몫이 잔고에 하나도 없을 때만 닫는다
            ok, drop, claimed = held - self._others(pos, today) <= 0, None, None
        else:
            drop, claimed = base - held, total - (bsold or 0)
            ok = drop >= claimed
        if ok:
            self._hold.pop(pos["id"], None)
            self._close(now, pos, reason, total, amount)
            return True
        first = self._hold.setdefault(pos["id"], now)
        if not decisive and (now - first).total_seconds() < SETTLE_WAIT:
            return False  # 잔고 반영이 늦을 수 있어 조금 더 본다
        self._hold.pop(pos["id"], None)
        tf = self._tf(pos, today)
        before = pos["sold_qty"]
        if base is None:
            real = before
            why = f"{reason} 체결로 보였지만 주문 전 잔고 기준이 없고 키움에 이 종목이 {held}주 있어 판단하지 못했습니다"
        else:
            real = max(before, min(total, (bsold or 0) + max(0, drop)))
            why = f"{reason} 체결로 보였지만 키움 잔고가 {max(0, drop)}주만 줄었습니다 (체결로 보인 것 {claimed}주) · 실제로 줄어든 만큼만 기록"
        avg = (amount - pos["sold_amount"]) / (total - before) if total > before else 0
        self._need_check(now, pos, why, sold_qty=real, sold_amount=pos["sold_amount"] + (real - before) * avg,
                         target_filled=0, old_target=None, target_order=None, exit_order=None,
                         claim_qty=total, claim_amount=amount, claim_reason=reason)
        if tf and real == before:
            self.log(now, f"({pos['name']} 오늘 목표가 체결로 보인 {tf}주도 반영하지 않았습니다)")
        return False

    def _recheck(self, now: dt.datetime, mode: str, today: str) -> None:
        """체결 오인 의심으로 '확인 필요'가 된 포지션: 잔고가 체결로 보인 만큼 줄었으면 (반영이 늦었던 것) 스스로 닫는다."""
        for pos in self.positions("active"):
            if pos["status"] != "check" or pos.get("claim_qty") is None or pos.get("unsure"):
                continue
            held = self._qty(now, mode, pos["code"])
            if held is None:
                continue
            if pos.get("base_qty") is None:
                ok = held - self._others(pos, today) <= 0
            else:
                ok = pos["base_qty"] - held >= pos["claim_qty"] - (pos["base_sold"] or 0)
            if ok:
                self.log(now, f"{pos['name']} 키움 잔고가 체결만큼 줄어 '확인 필요'를 풀고 정리합니다 (잔고 반영이 늦었던 것)")
                self._close(now, pos, pos["claim_reason"] or pos["exit_reason"] or "목표", pos["claim_qty"], pos["claim_amount"])

    def _watch_holdings(self, now: dt.datetime, holdings: list[dict], actives: list[dict]) -> None:
        """키움 잔고에만 있는 종목 · 앱 기록보다 많은 종목 → 기록·알림 (같은 종목은 하루 한 번). 화면용 목록도 남긴다."""
        today = now.date().isoformat()
        exp: dict[str, int] = {}
        for p in actives:  # 앱이 들고 있다고 아는 최대 수량
            exp[p["code"]] = exp.get(p["code"], 0) + max(0, p["qty"] - p["sold_qty"])
        busy = {r["code"] for r in self._q("SELECT code FROM at_plans WHERE status IN ('ordered', 'partial', 'unknown')")}
        rows, seen = [], {}
        for h in holdings:
            code, held = h["code"], int(h.get("qty") or 0)
            if held <= 0 or code in busy:  # 매수 체결을 아직 반영 중일 수 있는 종목은 뺀다
                continue
            want = exp.get(code)
            if want is None:
                kind, n = "untracked", held
            elif held > want:
                kind, n = "extra", held - want
            else:
                continue
            key = f"{code}:{kind}"
            seen[key] = self._odd.get(key, 0) + 1
            if seen[key] < 2:
                continue  # 체결 반영이 늦을 수 있어 두 번 연속 보일 때만
            rows.append({"code": code, "name": h.get("name") or code, "kind": kind, "qty": n, "held": held,
                         "avg_price": h.get("avg_price"), "price": h.get("price"), "ret": h.get("ret")})
        self._odd = seen
        new = [r for r in rows if self._warn(now, f"hold:{r['code']}")]
        for r in new:
            if r["kind"] == "untracked":
                self.log(now, f"⚠ 키움에만 있는 종목 {r['name']}({r['code']}) {r['qty']}주 · 앱이 산 기록이 없어 "
                              "목표·손절·기간 규칙 없이 방치됩니다 — 키움 앱에서 확인하세요")
            else:
                self.log(now, f"⚠ 키움 잔고가 앱 기록보다 많음 {r['name']}({r['code']}) 키움 {r['held']}주 / 앱 "
                              f"{r['held'] - r['qty']}주 · 남는 {r['qty']}주는 규칙 없이 방치됩니다 — 키움 앱에서 확인하세요")
        if new:
            self.notify(f"⚠ 키움 잔고 불일치 {len(new)}종목",
                        ", ".join(f"{r['name']} {r['qty']}주" for r in new[:6]) + " · 앱 규칙 없이 방치된 주식 (키움 앱에서 확인)")
        self._set_state(untracked=rows, untracked_at=now.isoformat(timespec="seconds"))

    def _reconcile(self, now: dt.datetime, mode: str, ex: dict | None = None, force: bool = False) -> None:
        """키움 잔고와 맞춰 본다: 우리가 낸 주문이 아닌데 수량이 줄었으면 키움에서 직접 판 것.
        반대로 키움에만 있거나 더 많은 종목은 기록·알림 (_watch_holdings)."""
        if not force and self._last_rec and (now - self._last_rec).total_seconds() < RECONCILE_EVERY:
            return
        self._last_rec = now
        holdings = self._balance(now, mode).get("holdings") or []
        held = {h["code"]: int(h.get("qty") or 0) for h in holdings}
        today = now.date().isoformat()
        for pos in [p for p in self.positions("active") if p["status"] == "open" and not p.get("unsure")]:
            tf = self._tf(pos, today)
            expected = pos["qty"] - pos["sold_qty"] - tf
            have = held.get(pos["code"], 0)
            if have >= expected:
                self._miss.pop(pos["id"], None)
                continue
            first = self._miss.get(pos["id"])
            if first is None or (now - first).total_seconds() > 300 or first.date() != now.date():
                self._miss[pos["id"]] = now  # 처음 (또는 오래전에 한 번 본 것은 없던 것으로)
                if not force:
                    continue  # 잔고 반영이 늦을 수 있어 한 번 더 본다
            self._miss.pop(pos["id"], None)
            diff = expected - have
            if ex is None:
                ex = self.kw.executions(mode)
            ours = self._known_orders(today) | {p["target_order"] for p in self.positions("active")} | {
                p["exit_order"] for p in self.positions("active")}
            sells = [e for no, e in ex.items() if e.get("code") == pos["code"] and e.get("side") == "sell"
                     and e.get("kind") != "cancel" and no not in ours and e["qty"] > 0]
            if sells:
                price = sum(e["qty"] * e["price"] for e in sells) / sum(e["qty"] for e in sells)
            else:
                price = (self.kw.quotes(mode, [pos["code"]]).get(pos["code"]) or pos["entry_price"])
            sold_qty = pos["sold_qty"] + tf + diff
            amount = pos["sold_amount"] + tf * pos["target_price"] + diff * price
            if pos["target_order"] and pos["target_date"] == today:
                try:
                    self._send(now, self.kw.cancel, mode, pos["code"], pos["target_order"])
                except Exception:  # noqa: BLE001
                    pass
            if have <= 0:
                self._close(now, pos, "키움에서 직접 매도", sold_qty, amount)
            else:
                self._upd("at_positions", pos["id"], now, sold_qty=sold_qty, sold_amount=amount,
                          target_order=None, target_filled=0, target_date=None)
                self.log(now, f"키움에서 직접 일부 매도 {pos['name']} {diff}주 · 남은 {have}주")
                if dt.time(9, 0) <= now.time() < STOP_UNTIL:
                    self._place_target(now, self._q("SELECT * FROM at_positions WHERE id = ?", (pos["id"],))[0], mode, today,
                                       after_cancel=True)
        self._watch_holdings(now, holdings, self.positions("active"))

    # ------------------------------------------------------------ 체결 확인
    def _close(self, now: dt.datetime, pos: dict, reason: str, sold_qty: int, sold_amount: float) -> None:
        price = sold_amount / sold_qty if sold_qty else None
        ret = (sold_amount / (pos["entry_price"] * sold_qty) - 1) * 100 if sold_qty else None  # 판 수량 기준
        self._upd("at_positions", pos["id"], now, status="closed", sold_qty=sold_qty, sold_amount=sold_amount,
                  exit_date=now.date().isoformat(), exit_price=price, ret=ret, exit_reason=reason, target_order=None,
                  target_filled=0, old_target=None, unsure=None, unsure_qty=None, unsure_at=None, unsure_seen=None,
                  check_msg=None, carry=None, claim_qty=None, claim_amount=None, claim_reason=None)
        self._hold.pop(pos["id"], None)
        slip = (price / pos["exit_ref"] - 1) * 100 if price and pos.get("exit_ref") else None
        self.log(now, f"{reason} 매도 체결 {pos['name']} {price:,.0f}원 · {ret:+.2f}%"
                 + (f" · 기준가 대비 {slip:+.2f}%" if slip is not None else ""))
        self.notify(f"{reason} · {pos['name']} {ret:+.2f}%", f"{sold_qty}주 {price:,.0f}원에 매도 (수수료·세금 전)")

    def _open_pos(self, now: dt.datetime, mode: str, p: dict, qty: int, price: float, sid, today: str,
                  target: bool = True) -> int:
        tgt, stop = self._levels(price, sid)
        pid = self._x("""INSERT INTO at_positions (plan_id, code, name, qty, entry_date, entry_price, target_price,
                         stop_price, status, created_at, updated_at, strategy_id, hold_days)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (p["id"], p["code"], p["name"], qty, today, price, tgt, stop, "open",
                       self._ts(now), self._ts(now), sid, self.rules(sid)["hold"]))
        self.log(now, f"매수 체결 {p['name']} {qty}주 {price:,.0f}원")
        self.notify(f"매수 체결 · {p['name']}", f"{qty}주 {price:,.0f}원 · 목표 {tgt:,}원 · 손절 {stop:,.0f}원")
        if target:
            self._place_target(now, self._q("SELECT * FROM at_positions WHERE id = ?", (pid,))[0], mode, today)
        return pid

    def _take_buy(self, now: dt.datetime, mode: str, p: dict, e: dict, sid, today: str) -> None:
        """매수 체결 반영. 처음이면 포지션을 열고, 더 체결됐으면 보유 수량을 늘려 목표가 주문을 새 수량으로 바꾼다."""
        poss = self._q("SELECT * FROM at_positions WHERE plan_id = ? ORDER BY id", (p["id"],))
        extra = int(e["qty"]) - sum(x["qty"] for x in poss)
        if extra <= 0:
            return
        if not poss:
            self._open_pos(now, mode, p, int(e["qty"]), e["price"], sid, today)
            return
        # 더 체결된 몫의 가격 = 전체 평균에서 이미 반영한 몫을 뺀 것
        xp = (e["qty"] * e["price"] - sum(x["qty"] * x["entry_price"] for x in poss)) / extra
        if not 0.5 * e["price"] <= xp <= 1.5 * e["price"]:
            xp = e["price"]
        opn = [x for x in poss if x["status"] == "open"]
        if not opn:  # 앞서 산 몫은 이미 팔았거나 파는 중·확인 필요 → 더 체결된 몫은 새 포지션으로 (기록을 건드리지 않게)
            self._open_pos(now, mode, p, extra, xp, sid, today)
            return
        pos = opn[-1]
        qty = pos["qty"] + extra
        price = (pos["qty"] * pos["entry_price"] + extra * xp) / qty
        tgt, stop = self._levels(price, sid)
        self.log(now, f"매수 추가 체결 {p['name']} +{extra}주 · 보유 {qty}주")
        live = bool(pos["target_order"]) and pos["target_date"] == today
        if pos.get("unsure") or pos.get("old_target"):  # 목표가 주문 상태를 아직 모름 → 새로 걸지 않는다 (확인되면 다시 건다)
            self._upd("at_positions", pos["id"], now, qty=qty, entry_price=price, stop_price=stop)
            return
        if not live:
            self._upd("at_positions", pos["id"], now, qty=qty, entry_price=price, target_price=tgt, stop_price=stop)
            self._place_target(now, self._q("SELECT * FROM at_positions WHERE id = ?", (pos["id"],))[0], mode, today)
            return
        tno = pos["target_order"]
        cancel = self._cancel(now, mode, pos, tno)
        done, f = False, None
        for k in range(4 if cancel else 1):
            if k:
                time.sleep(self.retry_wait)
            done, f = self._finished(mode, tno, pos["code"])
            if done:
                break
        tf = max(pos["target_filled"], f or 0)
        if done:  # 옛 목표가 주문이 끝난 게 확인됨 → 그동안 팔린 수량은 판 것으로 하고 새 수량으로 다시 건다
            self._upd("at_positions", pos["id"], now, qty=qty, entry_price=price, target_price=tgt, stop_price=stop,
                      target_order=None, target_date=None, target_filled=0, sold_qty=pos["sold_qty"] + tf,
                      sold_amount=pos["sold_amount"] + tf * pos["target_price"])
            self._place_target(now, self._q("SELECT * FROM at_positions WHERE id = ?", (pos["id"],))[0], mode, today,
                               after_cancel=True)
        else:  # 취소가 아직 확인 안 됨 → 옛 주문을 계속 따라가고, 끝난 게 확인되면 (target_date 비움) 장중에 새로 건다
            self._upd("at_positions", pos["id"], now, qty=qty, entry_price=price, target_price=tgt, stop_price=stop,
                      target_order=None, target_date=None, old_target=tno, target_filled=tf)
            self.log(now, f"{pos['name']} 옛 목표가 주문 취소가 아직 확인되지 않아 새 목표가는 확인 뒤에 겁니다")

    def sync(self, now: dt.datetime, mode: str) -> None:
        self._sync(now, mode, self.kw.executions(mode))

    def _sync(self, now: dt.datetime, mode: str, ex: dict, decisive: bool = False) -> None:
        today = now.date().isoformat()
        self._checked = set()
        self._exc = (now, mode, ex)  # 이번 확인에서 손절·매도도 같이 쓴다
        seen = ex
        if self._q("""SELECT 1 FROM at_plans WHERE status = 'unknown' UNION ALL
                      SELECT 1 FROM at_positions WHERE status != 'closed' AND unsure IS NOT NULL LIMIT 1"""):
            seen = self._seen(mode, ex, now)  # 결과 모르는 주문이 있을 때만 미체결도 본다 (계좌 조회 아끼기)
        self._resolve_buys(now, seen, today)
        self._track_old_targets(now, mode, ex, today)
        self._resolve_unsure(now, mode, seen, today, decisive)
        self._recheck(now, mode, today)
        # 매수 체결 (여러 날 유효한 지정가 주문도 있으니 날짜가 아니라 상태로 찾는다)
        first = (self.settings()["strategies"] or [{"id": None}])[0]["id"]
        for p in self._q("SELECT * FROM at_plans WHERE status IN ('ordered', 'partial')"):
            sid = p["strategy_id"] or first
            e = self._ex_for(ex, p["order_no"], p["code"], "buy")
            if not e or e["qty"] <= 0:
                continue
            self._take_buy(now, mode, p, e, sid, today)
            self._upd("at_plans", p["id"], now, status="filled" if e["left"] == 0 else "partial")
        # 매도 체결 (닫기 전에 키움 잔고로 한 번 더 확인)
        for pos in self.positions("active"):
            tf, tp = self._tf(pos, today), pos["target_price"]
            if pos["status"] == "closing" and pos["exit_order"]:
                e = self._ex_for(ex, pos["exit_order"], pos["code"], "sell")
                if not e or e["qty"] <= 0 or e["left"] != 0:
                    continue
                need = pos["qty"] - pos["sold_qty"] - tf
                total, amount = pos["sold_qty"] + tf + e["qty"], pos["sold_amount"] + tf * tp + e["qty"] * e["price"]
                if e["qty"] >= need or e["qty"] >= int(e.get("ordered") or need):  # 낸 주문은 다 체결 (잔고 한도로 덜 낸 경우 포함)
                    if e["qty"] < need:
                        self.log(now, f"{pos['name']} 키움 잔고 한도로 {e['qty']}주만 팔았고 다 체결 · 모자란 {need - e['qty']}주는 키움에 없어 판 것으로 치지 않음")
                    self._settle(now, mode, pos, pos["exit_reason"], total, amount, decisive)
                elif not decisive:  # 매도 주문이 덜 팔고 끝났다 (취소 등) → 판 만큼만 기록하고 사람에게
                    self._need_check(now, pos, f"{pos['exit_reason']} 매도 주문이 {e['qty']}주만 체결되고 끝남 · 남은 {need - e['qty']}주",
                                     exit_order=None, sold_qty=pos["sold_qty"] + e["qty"],
                                     sold_amount=pos["sold_amount"] + e["qty"] * e["price"])
            elif pos["status"] == "open" and pos["target_order"] and pos["target_date"] == today:
                e = self._ex_for(ex, pos["target_order"], pos["code"], "sell")
                if e and e["qty"] > 0:
                    if e["qty"] >= pos["qty"] - pos["sold_qty"]:
                        self._settle(now, mode, pos, "목표", pos["sold_qty"] + e["qty"],
                                     pos["sold_amount"] + e["qty"] * e["price"], decisive)
                    elif e["qty"] > pos["target_filled"]:
                        self._upd("at_positions", pos["id"], now, target_filled=e["qty"])
        for pid in [k for k in self._hold if k not in self._checked]:  # 다시 확인할 일이 없어진 것은 지운다
            self._hold.pop(pid, None)
        for k in [k for k in self._wait if k not in self._touched]:  # 지난 확인 뒤 다시 안 미룬 기록 (다른 날 것 포함)
            self._wait.pop(k, None)
        self._touched = set()
        self._ensure_targets(now, mode, today)

    def _check_stops(self, now: dt.datetime, mode: str) -> None:
        """손절. '확인 필요' 종목은 팔지 않고(무엇이 내 주식인지 확실치 않음) 손절선 아래면 알린다 (_check_stop_alert)."""
        opens = [p for p in self.positions("active") if p["status"] in ("open", "check")]
        if not opens:
            return
        px = self.kw.quotes(mode, sorted({p["code"] for p in opens}))
        for pos in opens:
            v = px.get(pos["code"])
            if not (v and pos["stop_price"] and v <= pos["stop_price"]):
                continue
            if pos["status"] == "open":
                self._sell_all(now, pos, mode, "손절", ref=v)
            else:
                self._check_stop_alert(now, pos, v)

    def _check_stop_alert(self, now: dt.datetime, pos: dict, v: float) -> None:
        """확인 필요 종목이 손절선 아래: 처음 한 번 + 손절선 대비 3%p 더 떨어질 때마다 다시 (하루 최대 4번)."""
        today = now.date().isoformat()
        step = int((pos["stop_price"] - v) / pos["stop_price"] * 100 // CHECK_STOP_STEP)
        rec = dict(self._state().get("check_stop") or {})
        r = rec.get(pos["code"])
        if r and r.get("d") == today and (step <= r["k"] or r["n"] >= CHECK_STOP_MAX):
            return
        n = (r["n"] if r and r.get("d") == today else 0) + 1
        rec = {k: x for k, x in rec.items() if x.get("d") == today}
        rec[pos["code"]] = {"d": today, "k": step, "n": n}
        self._set_state(check_stop=rec)
        gap = (v / pos["stop_price"] - 1) * 100
        self.log(now, f"⚠ 확인 필요 종목 {pos['name']}이 손절선 아래 ({v:,.0f}원 · 손절선 대비 {gap:+.1f}%) — 자동으로 팔지 않습니다")
        self.notify(f"⚠ 손절선 아래 · 확인 필요 {pos['name']}",
                    f"지금 {v:,.0f}원 (손절 {pos['stop_price']:,.0f}원, {gap:+.1f}%). 확인 필요 상태라 자동으로 팔지 않습니다 — "
                    "키움 앱에서 확인해 직접 정리해 주세요")

    def _trade_dates(self):
        """시세 데이터의 거래일 목록 (패널이 바뀔 때만 다시 계산 — 화면 새로고침마다 수백만 줄을 훑지 않게)."""
        import numpy as np

        panel = self.get_runner("kr").panel
        c = getattr(self, "_udates", None)
        if c is None or c[0] is not panel:
            c = (panel, np.unique(panel.dates))
            self._udates = c
        return c[1]

    def _held_days(self, entry_date: str, today: dt.date) -> int:
        """체결일 다음부터 오늘까지 거래일 수 (오늘이 거래일이면 오늘 포함).
        시세 데이터의 날짜로 세되, 데이터가 어제까지 안 들어와 있으면(갱신 지연) 거래소 달력으로 센다
        — 데이터가 늦어 하루 덜 세면 기간 만료 매도가 하루 늦어지기 때문."""
        cal = 0
        d = dt.date.fromisoformat(entry_date)
        while True:
            d = next_trading_day(d)
            if d >= today:
                break
            cal += 1
        cal += 1 if is_trading_day(today) else 0
        try:
            import numpy as np

            u = self._trade_dates()
            prev_td = today - dt.timedelta(days=1)
            while not is_trading_day(prev_td):
                prev_td -= dt.timedelta(days=1)
            if len(u) == 0 or u.max() < np.datetime64(prev_td.isoformat()):
                return cal  # 데이터가 밀려 있음 → 달력
            n = int(((u > np.datetime64(entry_date)) & (u < np.datetime64(today.isoformat()))).sum())
            return n + (1 if is_trading_day(today) else 0)
        except Exception:  # noqa: BLE001
            return cal

    def _err(self, now: dt.datetime, name: str, e: Exception) -> None:
        """단계 오류: 기록에 남기고, 같은 오류는 10분에 한 번만 (20초마다 쌓이지 않게)."""
        key = f"{name}:{str(e)[:80]}"
        last = self._errs.get(key)
        if last is None or (now - last).total_seconds() >= 600:
            self._errs[key] = now
            self.log(now, f"⚠ {name} 오류: {e}")
        self._set_state(last_error=f"{now.isoformat(timespec='seconds')} {name}: {str(e)[:200]}")

    def _risk(self, code: str, now: dt.datetime) -> str | None:
        """관리종목·거래정지 등 이유 (키움 종목정보). 조회가 안 되면 None (후보는 그대로)."""
        fn = getattr(self.kw, "stock_risk", None)
        if fn is None:
            return None
        try:
            return fn(self.settings()["account"], code)
        except Exception as e:  # noqa: BLE001
            if self._once(now, f"risk:{str(e)[:40]}"):
                self.log(now, f"위험 종목 확인 실패 ({code}): {e} — 후보는 그대로 둡니다")
            return None

    # ------------------------------------------------------------ 안전장치
    def _loss_guard(self, now: dt.datetime, mode: str) -> None:
        """하루 손실 한도: 전날 마감 계좌 금액보다 daily_loss_pct% 이상 줄면 다음 거래일 매수를 하루 쉰다.

        자동매매를 끄지는 않는다 (보유 종목 규칙·후보 만들기는 계속, 하루 쉰 뒤 저절로 다시 산다).
        """
        s = self.settings()
        pct = float(s.get("daily_loss_pct") or 0)
        st = self._state()
        today = now.date().isoformat()
        if pct <= 0 or not s["enabled"] or st.get("halted") == today:
            return
        if st.get("loss_check") and (now - dt.datetime.fromisoformat(st["loss_check"])).total_seconds() < 300:
            return
        self._set_state(loss_check=now.isoformat(timespec="seconds"))
        prev = self._q("SELECT assets FROM at_equity WHERE date < ? ORDER BY date DESC LIMIT 1", (today,))
        if not prev:
            return
        base = float(prev[0]["assets"])
        assets = self._balance(now, mode).get("assets")
        if not assets or base <= 0:
            return
        chg = (float(assets) / base - 1) * 100
        if chg <= -pct:
            why = f"전날 마감 {base / 1e4:,.0f}만 → 지금 {float(assets) / 1e4:,.0f}만 ({chg:+.1f}%)"
            self._set_state(halted=today, halted_why=why)
            self.log(now, f"⚠ 하루 손실 한도({pct:g}%) 도달 · {why} → 다음 거래일 매수를 하루 쉽니다 "
                          "(보유 종목의 목표·손절·기간만료는 계속, 그다음 날부터 저절로 다시 삽니다)")
            self.notify("⚠ 하루 손실 한도 도달 · 다음 거래일 매수 쉼",
                        f"{why}\n보유 종목 규칙은 계속 · 하루 쉰 뒤 저절로 다시 삽니다 (바로 다시 사려면 앱에서 '켜기')")

    def _buy_paused(self, now: dt.datetime, tstr: str) -> bool:
        """손실 한도로 오늘 매수를 쉬어야 하면 후보를 '건너뜀'으로 표시하고 True (멈춘 뒤 첫 매수 시각 한 번만)."""
        st = self._state()
        h = st.get("halted")
        if not h or h > tstr or st.get("halt_skipped") == h:
            return False
        self._set_state(halt_skipped=h)
        why = st.get("halted_why") or ""
        for p in self.live_plans(tstr) + [x for x in self.plans(tstr) if x["status"] == "proposed"]:
            if p["status"] in ("proposed", "approved"):
                self._upd("at_plans", p["id"], now, status="skipped", msg=f"손실 한도로 하루 쉼 ({why})"[:200])
        self.log(now, f"손실 한도로 오늘 매수는 쉽니다 ({why}) · 내일부터 다시 삽니다")
        return True

    def emergency(self, now: dt.datetime, sell_all: bool = False) -> dict:
        """비상 정지: 새 매수 끔 · 오늘 낸 매수 주문 취소 · 대기 후보 취소 · (sell_all) 보유 전부 시장가."""
        with self.lock:
            s = self.settings()
            mode = s["account"]
            self._save({**s, "enabled": False})
            self._set_state(halted=now.date().isoformat(), halted_why="비상 정지 버튼")
            cancelled = failed = sold = unsure = 0
            if self._q("SELECT id FROM at_plans WHERE status = 'unknown' LIMIT 1"):
                try:  # 결과 모르는 매수 주문이 실제로 들어갔으면 주문번호를 찾아 취소할 수 있게
                    self._resolve_buys(now, self._seen(mode, self.kw.executions(mode)), now.date().isoformat())
                except Exception:  # noqa: BLE001
                    pass
            for p in self._q("SELECT * FROM at_plans WHERE status IN ('proposed','approved','ordered','partial','unknown')"):
                if p["status"] == "unknown":  # 주문번호를 몰라 취소할 수 없다 → 그대로 두고 체결되면 규칙대로 관리
                    unsure += 1
                    self.log(now, f"⚠ 결과 모르는 매수 주문 {p['name']} — 취소하지 못했습니다. 키움 앱에서 확인하세요")
                    continue
                if p["status"] in ("ordered", "partial") and p["order_no"]:
                    try:
                        self._send(now, self.kw.cancel, mode, p["code"], p["order_no"])
                    except Exception as e:  # noqa: BLE001
                        failed += 1
                        self.log(now, f"⚠ 매수 주문 취소 실패 {p['name']}: {e}")
                        continue
                self._upd("at_plans", p["id"], now, status="cancelled", msg="비상 정지로 취소")
                cancelled += 1
            if sell_all:
                if not (is_trading_day(now.date()) and dt.time(9, 0) <= now.time() < EXPIRE_UNTIL):
                    self.log(now, "비상 정지: 장중(09:00~15:29)이 아니라 전부 팔기는 하지 않았습니다")
                else:
                    for pos in self.positions("active"):
                        if pos["status"] == "open" and self._sell_all(now, pos, mode, "비상 정지"):
                            sold += 1
            msg = f"비상 정지 · 새 매수 끔 · 매수 주문·후보 {cancelled}건 취소" + (f" · {sold}종목 시장가 매도" if sell_all else "")
            if failed:
                msg += f" · 취소 실패 {failed}건"
            if unsure:
                msg += f" · 결과 모르는 매수 주문 {unsure}건 (키움 앱에서 확인)"
            self.log(now, "⚠ " + msg)
            self.notify("⚠ 자동 매매 비상 정지", msg)
            return {"cancelled": cancelled, "sold": sold, "failed": failed, "unsure": unsure}

    def _remind_checks(self, now: dt.datetime) -> None:
        """'확인 필요'가 남아 있으면 08:50·12:00·14:00 에 다시 알린다 (방치 방지). 그 시각부터 10분 안에 한 번."""
        slot = next((x for x in reversed(CHECK_REMIND) if x <= now.time()), None)
        if slot is None or now > dt.datetime.combine(now.date(), slot) + dt.timedelta(minutes=10):
            return
        names = [p["name"] for p in self.positions("active") if p["status"] == "check"]
        names += [p["name"] for p in self._q("SELECT name FROM at_plans WHERE status = 'check'")]
        if names and self._warn(now, f"check-remind:{slot}"):
            self.notify(f"⚠ 확인 필요 {len(names)}종목 — 앱에서 정리해 주세요",
                        ", ".join(names[:6]) + " · 키움 앱에서 확인한 뒤 앱(매매 → 키움 자동)에서 정리 버튼을 눌러 주세요")

    def _due(self, now: dt.datetime, overdue: bool) -> list[dict]:
        """기간이 찬(overdue=True 면 이미 지난) 보유 종목. held 에 보유 거래일 수를 붙여 준다."""
        out = []
        for pos in self.positions("active"):
            if pos["status"] != "open":
                continue
            hold = pos["hold_days"] or self.rules(pos["strategy_id"])["hold"]
            held = self._held_days(pos["entry_date"], now.date())
            if held > hold or (not overdue and held >= hold):
                out.append({**pos, "held": held, "hold": hold})
        return out

    def _expire(self, now: dt.datetime, mode: str) -> bool:
        """기간 만료 종목을 시장가로. 모두 주문이 들어갔으면 True (남았으면 다음 확인 때 다시 시도)."""
        due = self._due(now, overdue=False)
        if self._once(now, f"expire-report:{now.date()}", every=86400):
            opens = [p for p in self.positions("active") if p["status"] == "open"]
            days = ", ".join(f"{p['name']} {self._held_days(p['entry_date'], now.date())}일째" for p in opens) or "없음"
            self.log(now, f"기간만료 확인 · 보유 {days} → 팔 것 {len(due)}종목")
        ok = True
        px = {}
        if due:
            try:
                px = self.kw.quotes(mode, sorted({p["code"] for p in due}))
            except Exception:  # noqa: BLE001 — 기준가는 기록용
                px = {}
        for pos in due:
            ok &= self._sell_all(now, pos, mode, "기간만료", ref=px.get(pos["code"]))
        return ok

    def _overdue(self, now: dt.datetime, mode: str) -> bool:
        """만료일이 지났는데 아직 들고 있는 종목 (15시대 매도를 놓쳤거나 체결이 안 됨), 그리고 전날 덜 판 종목(carry) → 장중에 시장가."""
        ok = True
        done = set()
        for pos in self._due(now, overdue=True):
            done.add(pos["id"])
            if self._once(now, f"overdue:{pos['id']}:{now.date()}", every=86400):
                self.log(now, f"기간만료 매도를 놓쳐 지금 팝니다: {pos['name']} (보유 {pos['held']}일째 / {pos['hold']}일)")
            ok &= self._sell_all(now, pos, mode, pos.get("carry") or "기간만료")
        for pos in self.positions("active"):
            if pos["status"] == "open" and pos.get("carry") and pos["id"] not in done:
                if self._once(now, f"carry:{pos['id']}:{now.date()}", every=86400):
                    self.log(now, f"전 거래일에 덜 판 {pos['carry']} 매도를 이어서 합니다: {pos['name']} "
                                  f"{pos['qty'] - pos['sold_qty']}주")
                ok &= self._sell_all(now, pos, mode, pos["carry"])
        return ok

    def _finish_day(self, now: dt.datetime, today: str) -> None:
        """15:31 마무리. 체결 조회가 안 되면 오류로 끝내 다음 확인(20초 뒤)에 다시 한다 — 체결을 모른 채
        '미체결'로 적으면 이미 산 종목을 다음 날 또 살 수 있다. 23시까지 안 되면 잔고 기준값으로 판정해 마무리."""
        mode = self.settings()["account"]
        try:
            ex = self.kw.executions(mode)
        except Exception as e:  # noqa: BLE001
            if now.time() < FINISH_GIVE_UP:
                raise RuntimeError(f"마감 체결 조회 실패 · 잠시 뒤 다시: {e}") from e
            ex = None
            self.log(now, f"⚠ 마감 체결 조회 실패: {e} — 잔고 기준으로 판정해 마무리")
        if ex is not None:
            self._sync(now, mode, ex, decisive=True)  # 마지막 체결까지 반영 (닫기 전 잔고 확인은 한 번에)
        else:
            self._resolve_unsure(now, mode, {}, today, decisive=True)
        for p in self._q("SELECT * FROM at_plans WHERE status IN ('ordered', 'partial', 'unknown')"):
            if p["status"] == "partial":
                self._upd("at_plans", p["id"], now, status="filled", msg="일부만 체결")
                continue
            until = p["valid_until"] or p["trade_date"]
            if p["status"] == "unknown" or ex is None:
                # 주문이 들어갔는지 모름: 체결 내역에 없었으니 주문 전 잔고(기준값)와 비교해 마지막 확인
                got = self._adopt_from_balance(now, mode, p, today)
                if got == "taken":
                    continue
                if got == "unreadable":
                    self._upd("at_plans", p["id"], now, status="unknown", msg="주문 결과 확인 못 함 · 다음 마감에 잔고로 다시 확인")
                    self.log(now, f"⚠ {p['name']} 매수 주문 결과를 확인하지 못했습니다 (잔고 조회 실패) — 키움 앱에서 확인하세요")
                    continue
                if got == "nobase":
                    self._upd("at_plans", p["id"], now, status="check", msg="확인 필요 · 주문 전 잔고 기준이 없어 판단 못 함 (키움 앱에서 확인)")
                    self.log(now, f"⚠ 확인 필요 · {p['name']} 매수 주문 결과를 모르고 주문 전 잔고 기준도 없어 판단하지 못했습니다")
                    if self._warn(now, f"check:{p['code']}"):
                        self.notify(f"⚠ 확인 필요 · {p['name']}", "매수 주문 결과를 모릅니다. 키움 앱에서 체결 여부를 확인하세요 (앱은 받지 않음)")
                    continue
                if today < until:
                    left = sum(1 for _ in self._trading_days(today, until))
                    self._upd("at_plans", p["id"], now, status="approved",
                              msg=f"주문이 확인되지 않음 · 내일 다시 주문 (남은 {left}일)")
                else:
                    self._upd("at_plans", p["id"], now, status="failed", msg="주문이 들어가지 않은 것으로 확인 (체결 내역에 없고 잔고도 그대로)")
                self.log(now, f"{p['name']} 결과 모르던 매수 주문: 체결 내역에 없고 잔고도 주문 전 그대로라 들어가지 않은 것으로 봅니다")
                continue
            if today < until:
                left = sum(1 for _ in self._trading_days(today, until))
                self._upd("at_plans", p["id"], now, status="approved",
                          msg=f"지정가에 안 닿음 · 내일 다시 주문 (남은 {left}일)")
            else:
                self._upd("at_plans", p["id"], now, status="nofill", msg="지정가에 닿지 않아 미체결 (주문은 장 마감에 사라짐)")
        # 오늘 목표가로 팔린 수량을 판 것으로 (주문은 장 마감에 사라진다 → 떼어 내서 마무리를 다시 해도 두 번 세지 않게)
        for pos in self.positions("active"):
            tf = self._tf(pos, today)
            upd = {"target_order": None, "old_target": None, "target_filled": 0} if (tf or pos.get("old_target")) else {}
            if tf:
                upd.update(sold_qty=pos["sold_qty"] + tf, sold_amount=pos["sold_amount"] + tf * pos["target_price"])
            if pos["status"] == "open" and pos.get("unsure"):  # 결과 모르던 목표가 주문은 장 마감에 사라진다 → 내일 다시
                upd.update(unsure=None, unsure_qty=None, unsure_at=None, unsure_seen=None)
            if upd:
                self._upd("at_positions", pos["id"], now, **upd)
        for pos in self.positions("active"):
            if pos["status"] != "closing" or pos.get("unsure"):  # 결과 모르는 매도는 위에서 잔고로 판정했다
                continue
            left_sent = pos["qty"] - (pos["base_sold"] if pos.get("base_sold") is not None else pos["sold_qty"])
            e = self._ex_for(ex, pos["exit_order"], pos["code"], "sell") if ex is not None else None
            if ex is None:  # 체결 내역 없이 잔고 기준값으로 판정
                done = self._drop_since_base(now, mode, pos)
                if done is None:
                    self._need_check(now, pos, "마감 체결 조회가 안 되고 잔고 기준으로도 판단하지 못했습니다", exit_order=None)
                    continue
                if done >= left_sent:
                    price = self._guess_price(mode, pos, {}, set())
                    self.log(now, f"{pos['name']} 체결 내역 없이 잔고로 다 판 것을 확인 ({price:,.0f}원 추정)")
                    self._close(now, pos, pos["exit_reason"], pos["sold_qty"] + left_sent, pos["sold_amount"] + left_sent * price)
                    continue
                price = self._guess_price(mode, pos, {}, set()) if done else 0
            else:
                done = int(e["qty"]) if e else 0
                price = e["price"] if e else 0
                if done and pos["qty"] - pos["sold_qty"] - done <= 0:
                    self._settle(now, mode, pos, pos["exit_reason"], pos["sold_qty"] + done, pos["sold_amount"] + done * price,
                                 decisive=True)
                    continue
                if done:  # 덜 팔림: 잔고가 실제로 줄어든 만큼만 기록 (체결 오인 방지)
                    real = self._drop_since_base(now, mode, pos)
                    if real is not None and real < done:
                        self.log(now, f"⚠ {pos['name']} 체결 {done}주로 보였지만 잔고는 {max(0, real)}주만 줄어 그만큼만 기록합니다")
                        done = max(0, real)
            left = pos["qty"] - pos["sold_qty"] - done
            self._upd("at_positions", pos["id"], now, status="open", exit_order=None, carry=pos["exit_reason"],
                      sold_qty=pos["sold_qty"] + done, sold_amount=pos["sold_amount"] + done * price)
            if done:  # 모의투자는 실제 거래량만큼만 체결 → 판 만큼 기록, 나머지는 다음 거래일 장 시작에 같은 사유로 이어서
                self.log(now, f"⚠ {pos['name']} {pos['exit_reason']} 매도 {done}주만 체결 ({price:,.0f}원) · "
                              f"남은 {left}주는 다음 거래일 장 시작에 이어서 팝니다")
                self.notify(f"{pos['exit_reason']} 일부 체결 · {pos['name']}", f"{done}주 체결 · 남은 {left}주는 다음 거래일 장 시작에")
            else:
                self.log(now, f"⚠ {pos['name']} {pos['exit_reason']} 매도가 체결되지 않았습니다 (거래 부족) · 다음 거래일 장 시작에 다시 팝니다")
        n = len(self.positions("active"))
        self.log(now, f"장 마감 정리 · 보유 {n}종목")

    def _drop_since_base(self, now: dt.datetime, mode: str, pos: dict) -> int | None:
        """이 매도 주문이 잔고에서 줄인 수량 (기준값 − 지금 잔고 − 그사이 목표가로 판 것). 판단 못 하면 None."""
        held = self._qty(now, mode, pos["code"])
        if held is None:
            return None
        if pos.get("base_qty") is None:  # 기준값이 없으면 이 종목 몫이 잔고에 하나도 없을 때만 '다 팔림'
            return pos["qty"] - pos["sold_qty"] if held - self._others(pos, now.date().isoformat()) <= 0 else None
        return pos["base_qty"] - held - (pos["sold_qty"] - (pos["base_sold"] or 0))

    def _adopt_from_balance(self, now: dt.datetime, mode: str, p: dict, today: str) -> str:
        """결과 모르는 매수 주문의 마지막 확인: 주문 전 잔고(기준값)보다 늘어난 만큼만 포지션으로 받는다.
        같은 종목 개인 보유분은 기준값에 들어 있어 가져오지 않는다.
        'taken': 받음 · 'none': 늘지 않음 · 'unreadable': 잔고를 못 읽음 · 'nobase': 기준값이 없음 (받지 않고 알리기만)."""
        if p.get("base_qty") is None:
            return "nobase"
        try:
            h = self._held(now, mode).get(p["code"]) or {}
        except Exception as e:  # noqa: BLE001
            if self._once(now, f"bal:{str(e)[:40]}"):
                self.log(now, f"⚠ 잔고 확인 실패: {e}")
            return "unreadable"
        held, base = int(h.get("qty") or 0), int(p["base_qty"])
        extra = held - base
        if extra <= 0:
            return "none"
        qty = min(extra, int(p["qty"]))
        avg, bavg = float(h.get("avg_price") or 0), float(p.get("base_avg") or 0)
        price = (held * avg - base * bavg) / extra if avg else 0  # 잔고 평균가에서 주문 전 몫을 뺀다
        if not price or not 0.5 * p["limit_price"] <= price <= 1.5 * p["limit_price"]:
            price = float(p["limit_price"])
        first = (self.settings()["strategies"] or [{"id": None}])[0]["id"]
        self._open_pos(now, mode, p, qty, price, p["strategy_id"] or first, today, target=False)
        self._upd("at_plans", p["id"], now, status="filled", msg=f"체결 내역엔 없지만 주문 전보다 잔고가 {extra}주 늘어 확인 ({qty}주)")
        self.log(now, f"⚠ {p['name']} 결과 모르던 매수 주문: 체결 내역엔 없지만 주문 전 {base}주 → 지금 {held}주 → {qty}주를 포지션으로 받음 "
                      "(목표가는 다음 거래일 아침에)")
        return "taken"

    @staticmethod
    def _trading_days(after: str, until: str):
        d = dt.date.fromisoformat(after)
        end = dt.date.fromisoformat(until)
        while True:
            d = next_trading_day(d)
            if d > end:
                return
            yield d

    # ------------------------------------------------------------ 주기 실행
    def tick(self, now: dt.datetime) -> None:
        with self.lock:
            self._tick(now)

    def _tick(self, now: dt.datetime) -> None:
        s = self.settings()
        mode = s["account"]
        if mode != "mock":
            return
        if not self.kw.view().get(mode, {}).get("saved"):
            return
        today = now.date()
        tstr = today.isoformat()
        st = self._state()
        t = now.time()
        # 1) 매수 후보
        if s["enabled"]:
            try:
                as_of = str(self.get_runner("kr").panel.dates.max())
            except Exception:  # noqa: BLE001
                as_of = None
            stamp = self._data_stamp() if as_of else None
            if as_of and (as_of != st.get("planned_asof") or stamp != st.get("planned_stamp")):
                tgt = next_trading_day(dt.date.fromisoformat(as_of))
                if now < dt.datetime.combine(tgt, APPROVE_UNTIL):
                    # 처음이거나, 데이터가 바뀌었으면(저녁 임시 → 아침 공식) 다시 본다 — 이미 있는 후보는 건너뛰고 새 신호만 더함
                    self.make_plans(now)
                self._set_state(planned_asof=as_of, planned_stamp=stamp)
            if (s["mode"] == "approve" and is_trading_day(today) and dt.time(8, 25) <= t < APPROVE_UNTIL
                    and st.get("remind") != tstr):
                waiting = [p for p in self.plans(tstr) if p["status"] == "proposed"]
                if waiting:
                    self.notify(f"승인 대기 {len(waiting)}종목", "08:45 까지 승인하지 않으면 오늘은 사지 않습니다")
                self._set_state(remind=tstr)
        if t >= APPROVE_UNTIL:  # 승인 시간이 지난 '승인 대기' 후보 정리 (늦게 만든 후보 포함)
            for p in self._q("SELECT id FROM at_plans WHERE status = 'proposed' AND trade_date <= ?", (tstr,)):
                self._upd("at_plans", p["id"], now, status="expired", msg="승인 시간(08:45)이 지나 사지 않음")
        if not is_trading_day(today):
            return
        # 단계마다 따로: 한 단계(예: 키움 체결 조회)가 오류여도 다른 단계(기간만료 매도 등)는 그대로 한다
        def step(name: str, fn) -> bool:
            try:
                fn()
                return True
            except Exception as e:  # noqa: BLE001
                self._err(now, name, e)
                return False

        step("확인 필요 알림", lambda: self._remind_checks(now))
        # 2) 08:50 매수 + 목표가 주문 다시 걸기 (늦게 켜졌으면 09:30 까지)
        if BUY_AT <= t < LATE_BUY_UNTIL and st.get("buy_done") != tstr:
            self._set_state(buy_done=tstr)
            if s["enabled"] and not self._buy_paused(now, tstr):
                if not step("매수 주문", lambda: self._place_buys(now, tstr, mode)):
                    self._set_state(buy_retry=tstr)  # 예: 주문가능금액 조회가 일시 오류 → 09:30 까지 다시
            step("잔고 확인", lambda: self._reconcile(now, mode, ex={}, force=True))  # 밤사이(시간외 등) 직접 판 것
            step("목표가 주문", lambda: self._refresh_targets(now, tstr, mode))
        elif BUY_AT <= t < LATE_BUY_UNTIL and st.get("buy_retry") == tstr and s["enabled"]:
            # 이미 낸 주문은 '주문함'·'확인 대기'라 다시 내지 않는다 (승인된 것 중 아직 안 낸 것만)
            if step("매수 주문", lambda: self._place_buys(now, tstr, mode)):
                self._set_state(buy_retry=None)
        # 3) 기간만료를 놓친 종목 → 장 시작에 (성공할 때까지 장중에 다시 시도)
        res: dict[str, bool] = {}
        if OPEN <= t < (EXPIRE_AT if mode == "mock" else EXPIRE_AT_REAL) and st.get("overdue_done") != tstr:
            if step("놓친 기간만료 매도", lambda: res.update(ok=self._overdue(now, mode))) and res.get("ok"):
                self._set_state(overdue_done=tstr)
        # 4) 보유기간 만료 (15:10~15:29, 모두 주문이 들어갈 때까지 그 안에서 다시 시도)
        exp_at = EXPIRE_AT if mode == "mock" else EXPIRE_AT_REAL
        if exp_at <= t < EXPIRE_UNTIL and st.get("expire_done") != tstr:
            if step("기간만료 매도", lambda: res.update(ok=self._expire(now, mode))) and res.get("ok"):
                self._set_state(expire_done=tstr)
        # 5) 장중: 체결 확인 · 잔고 · 손절
        if OPEN <= t < CLOSE_AT:
            step("체결 확인", lambda: self.sync(now, mode))
            step("잔고 확인", lambda: self._reconcile(now, mode))
            if t < STOP_UNTIL:
                step("손절 감시", lambda: self._check_stops(now, mode))
            step("하루 손실 한도", lambda: self._loss_guard(now, mode))
        # 6) 마무리
        if t >= CLOSE_AT and st.get("close_done") != tstr:
            # 마무리 안에서 마지막 체결 확인까지 한다. 체결 조회가 안 되면 다음 확인 때 다시 (끝난 것으로 치지 않는다)
            if step("마무리", lambda: self._finish_day(now, tstr)):
                self._set_state(close_done=tstr)
                step("계좌 금액 기록", lambda: self._snap_equity(now, tstr, mode))
        self._set_state(last_tick=now.isoformat(timespec="seconds"))


