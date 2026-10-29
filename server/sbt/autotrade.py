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
- 15:20 보유기간이 끝난 종목은 시장가 매도 (종가 동시호가).
- 15:31 마무리: 안 된 매수 주문은 '미체결'로, 목표가 주문은 내일 다시.

'전체 중지'(enabled=false)는 새 매수만 멈춘다. 이미 산 종목의 목표가·손절·기간 만료 매도는 계속한다.
수익률은 수수료·세금을 빼기 전 값이다.
"""
from __future__ import annotations

import datetime as dt
import re
import threading
from typing import Callable

from .api.store import _lock

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
}
# 전략 색 (차트·목록에서 같은 색). 상승 빨강·하락 파랑·강조 호박색과 겹치지 않게
COLORS = ["#A99CF5", "#3CC4B4", "#F08A5D", "#7EC8F2", "#E3B341", "#B5E655", "#FF7AB8"]
LIME = "#B5E655"  # 중간값 전략 (차트의 중간값 선과 같은 색)
NEW_STRATEGY = {"enabled": True, "per_trade": 3_000_000, "daily_max": 5, "rank": "value_asc"}
# 1회 금액: fixed = per_trade 원 고정, pct = 계좌(추정예탁자산)의 pct% (max_per_trade 원까지)
SIZE_DEFAULTS = {"size_mode": "fixed", "pct": 10.0, "max_per_trade": 20_000_000}
RANK_LABEL = {"value_asc": "거래대금 작은 순"}
MANUAL = ("직접 매도", "키움에서 직접 매도")  # 전략 규칙이 아닌 매도 (통계 따로)
RECONCILE_EVERY = 120  # 초: 키움 잔고와 맞춰 보는 간격
APPROVE_UNTIL = dt.time(8, 45)
BUY_AT = dt.time(8, 50)
OPEN = dt.time(9, 0)
LATE_BUY_UNTIL = dt.time(9, 30)
STOP_UNTIL = dt.time(15, 19)
EXPIRE_AT = dt.time(15, 20, 30)
EXPIRE_UNTIL = dt.time(15, 29)
CLOSE_AT = dt.time(15, 31)


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
        self._miss: dict[int, int] = {}  # 포지션 id → 잔고가 모자란 것을 본 횟수 (두 번 연속이면 직접 매도로 본다)
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
            db.execute("""CREATE TABLE IF NOT EXISTS at_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, text TEXT NOT NULL)""")
            # 여러 전략용 칸 (예전 데이터는 첫 전략으로 본다)
            for table, cols in (("at_plans", {"strategy_id": "INTEGER", "valid_until": "TEXT", "entry_kind": "TEXT"}),
                                ("at_positions", {"strategy_id": "INTEGER", "hold_days": "INTEGER"})):
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
        for k in ("strategy_id", "per_trade", "daily_max", "rank"):
            s.pop(k, None)
        return s

    def _save(self, s: dict) -> None:
        self.store.set_setting("autotrade", {k: v for k, v in s.items() if k in DEFAULTS})

    def set_settings(self, **kw) -> dict:
        """전체 켜기·끄기와 주문 방식. (예전 호출 호환: strategy_id·per_trade·daily_max 는 첫 전략에)"""
        s = self.settings()
        for k in ("enabled", "mode"):
            if kw.get(k) is not None:
                s[k] = kw[k]
        if s["mode"] not in ("approve", "auto"):
            raise ValueError("mode 는 approve 또는 auto")
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
    COST_PCT = 0.23  # 매수·매도 수수료 0.015% 씩 + 거래세 0.20% (백테스트와 같은 가정)

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
        plans = self._q("""SELECT * FROM at_plans WHERE trade_date = ? OR (status IN ('approved','ordered','partial')
                           AND COALESCE(valid_until, trade_date) >= ?) ORDER BY trade_date DESC, rank""",
                        (latest or "", today))
        for p in plans:
            p["strategy_id"] = p["strategy_id"] or first
        positions = self.positions()
        for p in positions:
            p["strategy_id"] = p["strategy_id"] or first
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
            "log": self._q("SELECT ts, text FROM at_log ORDER BY id DESC LIMIT 60"),
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
        if not force and now >= dt.datetime.combine(t, APPROVE_UNTIL):
            return []  # 이미 늦음
        status = "approved" if s["mode"] == "auto" else "proposed"
        taken = {p["code"] for p in self.positions("active")} | {
            p["code"] for p in self._q("""SELECT code FROM at_plans WHERE status IN ('proposed','approved','ordered','partial')
                                          AND COALESCE(valid_until, trade_date) >= ?""", (t.isoformat(),))}
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
            self.log(now, f"{t.month}/{t.day} 매수 후보 {n}종목 ({strat['name']}, 데이터 {as_of})")
        n = len(out)
        if only is None:
            self._set_state(planned_asof=as_of, last_plan={"trade_date": t.isoformat(), "signal_date": as_of, "count": n,
                                                           "per": per})
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
        orderable = (self.kw.cash(mode).get("orderable") or 0)
        placed = []
        for p in plans:
            need = p["qty"] * p["limit_price"] * 1.001
            if orderable < need:
                if today < (p["valid_until"] or p["trade_date"]):
                    self._upd("at_plans", p["id"], now, msg="주문가능금액 부족 · 내일 다시 시도")
                else:
                    self._upd("at_plans", p["id"], now, status="skipped", msg="주문가능금액 부족")
                continue
            try:
                no = self.kw.buy_limit(mode, p["code"], p["qty"], p["limit_price"])
            except Exception as e:  # noqa: BLE001
                self._upd("at_plans", p["id"], now, status="failed", msg=str(e)[:200])
                self.log(now, f"매수 주문 실패 {p['name']}: {e}")
                continue
            orderable -= need
            self._upd("at_plans", p["id"], now, status="ordered", order_no=no)
            placed.append(f"{p['name']} {p['qty']}주" + (f" @{p['limit_price']:,}" if p["entry_kind"] == "limit" else ""))
        if placed:
            self.log(now, "매수 주문: " + ", ".join(placed))
            self.notify(f"매수 주문 {len(placed)}건", ", ".join(placed[:6]) + " · 지정가")

    def _place_target(self, now: dt.datetime, pos: dict, mode: str, today: str) -> None:
        left = pos["qty"] - pos["sold_qty"]
        if left <= 0 or not pos["target_price"]:
            return
        try:
            no = self.kw.sell_limit(mode, pos["code"], left, pos["target_price"])
            self._upd("at_positions", pos["id"], now, target_order=no, target_date=today, target_filled=0)
        except Exception as e:  # noqa: BLE001
            self.log(now, f"목표가 주문 실패 {pos['name']}: {e}")

    def _refresh_targets(self, now: dt.datetime, today: str, mode: str) -> None:
        for pos in self.positions("active"):
            if pos["status"] == "open" and pos["target_date"] != today:
                self._place_target(now, pos, mode, today)

    def _sell_all(self, now: dt.datetime, pos: dict, mode: str, reason: str) -> None:
        if pos["target_order"] and pos["target_date"] == now.date().isoformat():
            try:
                self.kw.cancel(mode, pos["code"], pos["target_order"])
            except Exception as e:  # noqa: BLE001 — 이미 체결됐을 수 있다
                self.log(now, f"목표가 주문 취소 실패 {pos['name']}: {e}")
        left = pos["qty"] - pos["sold_qty"]
        if left <= 0:
            return
        try:
            no = self.kw.sell_market(mode, pos["code"], left)
        except Exception as e:  # noqa: BLE001
            self.log(now, f"{reason} 매도 주문 실패 {pos['name']}: {e}")
            self.notify(f"⚠ {reason} 매도 실패", f"{pos['name']}: {e}")
            return
        self._upd("at_positions", pos["id"], now, status="closing", exit_order=no, exit_reason=reason, target_order=None)
        self.log(now, f"{reason} 매도 주문 {pos['name']} {left}주 (시장가)")

    def manual_sell(self, pid: int, now: dt.datetime) -> None:
        """앱의 '지금 팔기': 목표가 주문 취소 → 시장가 매도. 통계는 '직접 매도'로 따로."""
        with self.lock:
            pos = self._q("SELECT * FROM at_positions WHERE id = ?", (pid,))
            if not pos:
                raise ValueError("보유 종목이 없습니다")
            pos = pos[0]
            if pos["status"] != "open":
                raise ValueError("이미 매도 중이거나 끝난 종목입니다")
            if not is_trading_day(now.date()) or not (dt.time(9, 0) <= now.time() < EXPIRE_UNTIL):
                raise ValueError("장중(09:00~15:29)에만 팔 수 있습니다")
            self._sell_all(now, pos, self.settings()["account"], "직접 매도")
            if self._q("SELECT status FROM at_positions WHERE id = ?", (pid,))[0]["status"] != "closing":
                raise ValueError("매도 주문을 내지 못했습니다. 기록을 확인하세요")

    def _reconcile(self, now: dt.datetime, mode: str, ex: dict | None = None, force: bool = False) -> None:
        """키움 잔고와 맞춰 본다: 우리가 낸 주문이 아닌데 수량이 줄었으면 키움에서 직접 판 것."""
        if not force and self._last_rec and (now - self._last_rec).total_seconds() < RECONCILE_EVERY:
            return
        self._last_rec = now
        opens = [p for p in self.positions("active") if p["status"] == "open"]
        if not opens:
            return
        held = {h["code"]: h["qty"] for h in self.kw.balance(mode)["holdings"]}
        today = now.date().isoformat()
        if ex is None:
            ex = self.kw.executions(mode)
        ours = {p["target_order"] for p in self.positions("active")} | {p["exit_order"] for p in self.positions("active")}
        for pos in opens:
            tf = pos["target_filled"] if pos["target_date"] == today else 0
            expected = pos["qty"] - pos["sold_qty"] - tf
            have = held.get(pos["code"], 0)
            if have >= expected:
                self._miss.pop(pos["id"], None)
                continue
            self._miss[pos["id"]] = self._miss.get(pos["id"], 0) + 1
            if self._miss[pos["id"]] < 2 and not force:
                continue  # 잔고 반영이 늦을 수 있어 한 번 더 본다
            self._miss.pop(pos["id"], None)
            diff = expected - have
            sells = [e for no, e in ex.items() if e.get("code") == pos["code"] and e.get("side") == "sell"
                     and no not in ours and e["qty"] > 0]
            if sells:
                price = sum(e["qty"] * e["price"] for e in sells) / sum(e["qty"] for e in sells)
            else:
                price = (self.kw.quotes(mode, [pos["code"]]).get(pos["code"]) or pos["entry_price"])
            sold_qty = pos["sold_qty"] + tf + diff
            amount = pos["sold_amount"] + tf * pos["target_price"] + diff * price
            if pos["target_order"] and pos["target_date"] == today:
                try:
                    self.kw.cancel(mode, pos["code"], pos["target_order"])
                except Exception:  # noqa: BLE001
                    pass
            if have <= 0:
                self._close(now, pos, "키움에서 직접 매도", sold_qty, amount)
            else:
                self._upd("at_positions", pos["id"], now, sold_qty=sold_qty, sold_amount=amount,
                          target_order=None, target_filled=0, target_date=None)
                self.log(now, f"키움에서 직접 일부 매도 {pos['name']} {diff}주 · 남은 {have}주")
                if dt.time(9, 0) <= now.time() < STOP_UNTIL:
                    self._place_target(now, self._q("SELECT * FROM at_positions WHERE id = ?", (pos["id"],))[0], mode, today)

    # ------------------------------------------------------------ 체결 확인
    def _close(self, now: dt.datetime, pos: dict, reason: str, sold_qty: int, sold_amount: float) -> None:
        price = sold_amount / sold_qty if sold_qty else None
        ret = (sold_amount / (pos["entry_price"] * pos["qty"]) - 1) * 100 if sold_qty else None
        self._upd("at_positions", pos["id"], now, status="closed", sold_qty=sold_qty, sold_amount=sold_amount,
                  exit_date=now.date().isoformat(), exit_price=price, ret=ret, exit_reason=reason, target_order=None)
        self.log(now, f"{reason} 매도 체결 {pos['name']} {price:,.0f}원 · {ret:+.2f}%")
        self.notify(f"{reason} · {pos['name']} {ret:+.2f}%", f"{sold_qty}주 {price:,.0f}원에 매도 (수수료·세금 전)")

    def sync(self, now: dt.datetime, mode: str) -> None:
        today = now.date().isoformat()
        ex = self.kw.executions(mode)
        # 매수 체결 (여러 날 유효한 지정가 주문도 있으니 날짜가 아니라 상태로 찾는다)
        first = (self.settings()["strategies"] or [{"id": None}])[0]["id"]
        for p in self._q("SELECT * FROM at_plans WHERE status IN ('ordered', 'partial')"):
            if not p["order_no"]:
                continue
            sid = p["strategy_id"] or first
            e = ex.get(p["order_no"])
            if not e or e["qty"] <= 0:
                continue
            pos = self._q("SELECT * FROM at_positions WHERE plan_id = ?", (p["id"],))
            if not pos:
                price = e["price"]
                tgt, stop = self._levels(price, sid)
                pid = self._x("""INSERT INTO at_positions (plan_id, code, name, qty, entry_date, entry_price, target_price,
                                 stop_price, status, created_at, updated_at, strategy_id, hold_days)
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                              (p["id"], p["code"], p["name"], e["qty"], today, price, tgt, stop, "open",
                               self._ts(now), self._ts(now), sid, self.rules(sid)["hold"]))
                self.log(now, f"매수 체결 {p['name']} {e['qty']}주 {price:,.0f}원")
                self.notify(f"매수 체결 · {p['name']}", f"{e['qty']}주 {price:,.0f}원 · 목표 {tgt:,}원 · 손절 {stop:,.0f}원")
                self._place_target(now, self._q("SELECT * FROM at_positions WHERE id = ?", (pid,))[0], mode, today)
            elif e["qty"] > pos[0]["qty"]:
                pos = pos[0]
                if pos["target_order"]:
                    try:
                        self.kw.cancel(mode, pos["code"], pos["target_order"])
                    except Exception:  # noqa: BLE001
                        pass
                tgt, stop = self._levels(e["price"], sid)
                self._upd("at_positions", pos["id"], now, qty=e["qty"], entry_price=e["price"],
                          target_price=tgt, stop_price=stop, target_order=None)
                self._place_target(now, self._q("SELECT * FROM at_positions WHERE id = ?", (pos["id"],))[0], mode, today)
            self._upd("at_plans", p["id"], now, status="filled" if e["left"] == 0 else "partial")
        # 매도 체결
        for pos in self.positions("active"):
            if pos["status"] == "closing" and pos["exit_order"]:
                e = ex.get(pos["exit_order"])
                if e and e["qty"] > 0 and e["left"] == 0:
                    self._close(now, pos, pos["exit_reason"], pos["sold_qty"] + e["qty"],
                                pos["sold_amount"] + e["qty"] * e["price"])
            elif pos["status"] == "open" and pos["target_order"] and pos["target_date"] == today:
                e = ex.get(pos["target_order"])
                if e and e["qty"] > pos["target_filled"]:
                    left = pos["qty"] - pos["sold_qty"]
                    if e["qty"] >= left:
                        self._close(now, pos, "목표", pos["sold_qty"] + e["qty"], pos["sold_amount"] + e["qty"] * e["price"])
                    else:
                        self._upd("at_positions", pos["id"], now, target_filled=e["qty"])

    def _check_stops(self, now: dt.datetime, mode: str) -> None:
        opens = [p for p in self.positions("active") if p["status"] == "open"]
        if not opens:
            return
        px = self.kw.quotes(mode, sorted({p["code"] for p in opens}))
        for pos in opens:
            v = px.get(pos["code"])
            if v and pos["stop_price"] and v <= pos["stop_price"]:
                self._sell_all(now, pos, mode, "손절")

    def _held_days(self, entry_date: str, today: dt.date) -> int:
        """체결일 다음부터 오늘까지 거래일 수 (실제 시세 데이터 + 오늘)."""
        try:
            dates = self.get_runner("kr").panel.dates
            import numpy as np

            u = np.unique(dates)
            n = int(((u > np.datetime64(entry_date)) & (u < np.datetime64(today.isoformat()))).sum())
        except Exception:  # noqa: BLE001
            n = 0
            d = dt.date.fromisoformat(entry_date)
            while True:
                d = next_trading_day(d)
                if d >= today:
                    break
                n += 1
        return n + (1 if is_trading_day(today) else 0)

    def _expire(self, now: dt.datetime, mode: str) -> None:
        for pos in self.positions("active"):
            hold = pos["hold_days"] or self.rules(pos["strategy_id"])["hold"]
            if pos["status"] == "open" and self._held_days(pos["entry_date"], now.date()) >= hold:
                self._sell_all(now, pos, mode, "기간만료")

    def _finish_day(self, now: dt.datetime, today: str) -> None:
        for p in self._q("SELECT * FROM at_plans WHERE status IN ('ordered', 'partial')"):
            if p["status"] == "partial":
                self._upd("at_plans", p["id"], now, status="filled", msg="일부만 체결")
                continue
            until = p["valid_until"] or p["trade_date"]
            if today < until:
                left = sum(1 for _ in self._trading_days(today, until))
                self._upd("at_plans", p["id"], now, status="approved",
                          msg=f"지정가에 안 닿음 · 내일 다시 주문 (남은 {left}일)")
            else:
                self._upd("at_plans", p["id"], now, status="nofill", msg="지정가에 닿지 않아 미체결 (주문은 장 마감에 사라짐)")
        for pos in self.positions("active"):
            if pos["status"] == "open" and pos["target_date"] == today:
                sold = pos["target_filled"]
                if sold:  # 목표가 일부 체결
                    self._upd("at_positions", pos["id"], now, sold_qty=pos["sold_qty"] + sold,
                              sold_amount=pos["sold_amount"] + sold * pos["target_price"], target_filled=0)
            if pos["status"] == "closing":
                self.log(now, f"⚠ {pos['name']} {pos['exit_reason']} 매도가 아직 체결되지 않았습니다. 내일 다시 확인합니다")
                self._upd("at_positions", pos["id"], now, status="open", exit_order=None)
        n = len(self.positions("active"))
        self.log(now, f"장 마감 정리 · 보유 {n}종목")

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
            if as_of and as_of != st.get("planned_asof"):
                tgt = next_trading_day(dt.date.fromisoformat(as_of))
                if now < dt.datetime.combine(tgt, APPROVE_UNTIL):
                    if not self.plans(tgt.isoformat()):
                        self.make_plans(now)
                    else:
                        self._set_state(planned_asof=as_of)
            if (s["mode"] == "approve" and is_trading_day(today) and dt.time(8, 25) <= t < APPROVE_UNTIL
                    and st.get("remind") != tstr):
                waiting = [p for p in self.plans(tstr) if p["status"] == "proposed"]
                if waiting:
                    self.notify(f"승인 대기 {len(waiting)}종목", "08:45 까지 승인하지 않으면 오늘은 사지 않습니다")
                self._set_state(remind=tstr)
        if not is_trading_day(today):
            return
        # 2) 08:50 매수 + 목표가 주문 다시 걸기 (늦게 켜졌으면 09:30 까지)
        if BUY_AT <= t < LATE_BUY_UNTIL and st.get("buy_done") != tstr:
            self._set_state(buy_done=tstr)
            if s["enabled"]:
                self._place_buys(now, tstr, mode)
            try:
                self._reconcile(now, mode, ex={}, force=True)  # 밤사이(시간외 등) 직접 판 것
            except Exception as e:  # noqa: BLE001
                self.log(now, f"잔고 확인 실패: {e}")
            self._refresh_targets(now, tstr, mode)
        # 3) 장중
        if OPEN <= t < CLOSE_AT:
            self.sync(now, mode)
            self._reconcile(now, mode)
            if t < STOP_UNTIL:
                self._check_stops(now, mode)
        # 4) 보유기간 만료
        if EXPIRE_AT <= t < EXPIRE_UNTIL and st.get("expire_done") != tstr:
            self._set_state(expire_done=tstr)
            self._expire(now, mode)
        # 5) 마무리
        if t >= CLOSE_AT and st.get("close_done") != tstr:
            self.sync(now, mode)
            self._set_state(close_done=tstr)
            self._finish_day(now, tstr)


