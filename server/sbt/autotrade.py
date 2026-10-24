"""자동 매매 (키움 모의투자). 백테스트와 같은 규칙으로 주문을 낸다.

하루 흐름 (거래일, 한국시간)
- 새 거래일 데이터가 들어오면 (저녁 임시값이든 아침 공식 값이든 먼저 온 쪽) 다음 거래일 '매수 후보'를 만든다.
  고른 전략의 그날 신호 → 이미 보유 중인 종목 제외 → 거래대금 작은 순 → 하루 최대 n종목.
  승인 모드면 휴대폰으로 알리고 08:45 까지 승인을 기다린다. 승인 안 한 것은 사지 않는다.
- 08:50 매수: 지정가 = 신호일 종가 × (1 + buy_limit_pct%) — 장전 동시호가라 시가가 그 아래면 시가에 체결된다.
  같은 시각, 보유 종목의 목표가 매도(지정가 +10%)를 다시 건다 (KRX 지정가 주문은 그날만 유효).
- 09:00~15:19 체결 확인 (체결되면 목표가 매도 주문), 손절가 이하면 시장가 매도.
- 15:20 보유기간이 끝난 종목은 시장가 매도 (종가 동시호가).
- 15:31 마무리: 안 된 매수 주문은 '미체결'로, 목표가 주문은 내일 다시.

'전체 중지'(enabled=false)는 새 매수만 멈춘다. 이미 산 종목의 목표가·손절·기간 만료 매도는 계속한다.
수익률은 수수료·세금을 빼기 전 값이다.
"""
from __future__ import annotations

import datetime as dt
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
    "strategy_id": None,
    "per_trade": 5_000_000,
    "daily_max": 10,
    "rank": "value_asc",  # 거래대금 작은 순
    "buy_limit_pct": 5.0,
}
RANK_LABEL = {"value_asc": "거래대금 작은 순"}
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
        s = {**DEFAULTS, **self.store.get_setting("autotrade", {})}
        if not s["strategy_id"]:
            s["strategy_id"] = next((x["id"] for x in self.store.list() if "v2b" in x["name"]), None)
        return s

    def set_settings(self, **kw) -> dict:
        s = self.settings()
        for k, v in kw.items():
            if k in DEFAULTS and k != "account" and v is not None:
                s[k] = v
        if s["mode"] not in ("approve", "auto"):
            raise ValueError("mode 는 approve 또는 auto")
        if s["per_trade"] < 100_000 or not 1 <= int(s["daily_max"]) <= 30:
            raise ValueError("1회 매수금은 10만원 이상, 하루 최대는 1~30종목")
        self.store.set_setting("autotrade", s)
        return s

    def rules(self) -> dict:
        """고른 전략의 청산 규칙: 목표 %, 손절 %, 보유 거래일."""
        s = self.settings()
        strat = self.store.get(s["strategy_id"]) if s["strategy_id"] else None
        if not strat:
            return {"target": 10.0, "stop": 7.0, "hold": 5}
        x = self.parse_text(strat["text"]).exit
        return {"target": x.target_pct, "stop": x.stop_pct, "hold": int(x.max_days)}

    def _levels(self, price: float) -> tuple[int, float]:
        r = self.rules()
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
            return self._q("SELECT * FROM at_plans WHERE trade_date = ? ORDER BY rank", (trade_date,))
        return self._q("SELECT * FROM at_plans ORDER BY trade_date DESC, rank LIMIT 100")

    def positions(self, status: str | None = None) -> list[dict]:
        if status == "active":
            return self._q("SELECT * FROM at_positions WHERE status != 'closed' ORDER BY entry_date, id")
        return self._q("SELECT * FROM at_positions ORDER BY (status = 'closed'), COALESCE(exit_date, entry_date) DESC, id DESC LIMIT 200")

    def _upd(self, table: str, rid: int, now: dt.datetime, **kw) -> None:
        sets = ", ".join(f"{k} = ?" for k in kw)
        self._x(f"UPDATE {table} SET {sets}, updated_at = ? WHERE id = ?", (*kw.values(), self._ts(now), rid))

    # ------------------------------------------------------------ 화면용
    def view(self) -> dict:
        s = self.settings()
        strat = self.store.get(s["strategy_id"]) if s["strategy_id"] else None
        latest = self._q("SELECT MAX(trade_date) d FROM at_plans")[0]["d"]
        closed = self._q("SELECT ret FROM at_positions WHERE status = 'closed' AND ret IS NOT NULL")
        rets = [c["ret"] for c in closed]
        return {
            "key_saved": bool(self.kw.view().get("mock", {}).get("saved")), "rules": self.rules(),
            "settings": s, "rank_label": RANK_LABEL.get(s["rank"], s["rank"]),
            "strategy": {"id": strat["id"], "name": strat["name"]} if strat else None,
            "plans": self.plans(latest) if latest else [],
            "plan_date": latest,
            "positions": self.positions(),
            "stats": {"n": len(rets), "win": sum(1 for r in rets if r > 0),
                      "avg": round(sum(rets) / len(rets), 2) if rets else None},
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
        if now >= deadline:
            raise ValueError("승인 시간(08:45)이 지났습니다")
        self._upd("at_plans", pid, now, status="approved" if ok else "rejected")

    def approve_all(self, trade_date: str, now: dt.datetime) -> int:
        n = 0
        for p in self.plans(trade_date):
            if p["status"] == "proposed":
                self.approve(p["id"], True, now)
                n += 1
        return n

    # ------------------------------------------------------------ 매수 후보
    def make_plans(self, now: dt.datetime, force: bool = False) -> list[dict]:
        s = self.settings()
        if not s["strategy_id"]:
            raise ValueError("자동 매매에 쓸 전략을 고르세요")
        strat = self.store.get(s["strategy_id"])
        if not strat:
            raise ValueError("고른 전략이 지워졌습니다")
        runner = self.get_runner("kr")
        as_of = str(runner.panel.dates.max())
        t = next_trading_day(dt.date.fromisoformat(as_of))
        if not force and now >= dt.datetime.combine(t, APPROVE_UNTIL):
            return []  # 이미 늦음
        spec = self.parse_text(strat["text"])
        rows = [r for r in runner.scan(spec, 1)["rows"] if r["signal_date"] == as_of]
        held = {p["code"] for p in self.positions("active")}
        rows = [r for r in rows if r["code"] not in held and r.get("close")]
        rows.sort(key=lambda r: (r.get("value") is None, r.get("value") or 0))
        rows = rows[: int(s["daily_max"])]
        status = "approved" if s["mode"] == "auto" else "proposed"
        out = []
        for k, r in enumerate(rows, 1):
            close = float(r["close"])
            limit = self.kw.round_tick(close * (1 + s["buy_limit_pct"] / 100), up=False)
            qty = int(s["per_trade"] // close)
            st, msg = (status, "") if qty >= 1 else ("skipped", "1주 가격이 1회 매수금보다 큼")
            self._x("""INSERT OR IGNORE INTO at_plans (trade_date, code, name, signal_date, rank, value, close, limit_price,
                       qty, status, msg, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (t.isoformat(), r["code"], r["name"], as_of, k, r.get("value"), close, limit, qty, st, msg,
                     self._ts(now), self._ts(now)))
            out.append(r)
        self._set_state(planned_asof=as_of)
        n = len(out)
        md = f"{t.month}/{t.day}"
        if n:
            names = ", ".join(r["name"] for r in out[:4]) + (f" 외 {n - 4}" if n > 4 else "")
            if s["mode"] == "approve":
                self.notify(f"{md} 매수 후보 {n}종목 · 승인 필요", f"{names}\n08:45 까지 앱(모투 → 자동 매매)에서 승인하세요")
            else:
                self.notify(f"{md} 자동 매수 예정 {n}종목", f"{names}\n08:50 에 주문합니다")
        self.log(now, f"{md} 매수 후보 {n}종목 ({strat['name']}, 신호일 {as_of})")
        return out

    # ------------------------------------------------------------ 주문
    def _place_buys(self, now: dt.datetime, today: str, mode: str) -> None:
        plans = [p for p in self.plans(today) if p["status"] == "approved"]
        for p in self.plans(today):
            if p["status"] == "proposed":
                self._upd("at_plans", p["id"], now, status="expired", msg="승인하지 않아 사지 않음")
        if not plans:
            return
        orderable = (self.kw.cash(mode).get("orderable") or 0)
        placed = []
        for p in plans:
            need = p["qty"] * p["limit_price"] * 1.001
            if orderable < need:
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
            placed.append(f"{p['name']} {p['qty']}주")
        if placed:
            self.log(now, "매수 주문: " + ", ".join(placed))
            self.notify(f"매수 주문 {len(placed)}건", ", ".join(placed[:6]) + " · 지정가(시가 체결 예상)")

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
        # 매수 체결
        for p in self.plans(today):
            if p["status"] not in ("ordered", "partial") or not p["order_no"]:
                continue
            e = ex.get(p["order_no"])
            if not e or e["qty"] <= 0:
                continue
            pos = self._q("SELECT * FROM at_positions WHERE plan_id = ?", (p["id"],))
            if not pos:
                price = e["price"]
                tgt, stop = self._levels(price)
                pid = self._x("""INSERT INTO at_positions (plan_id, code, name, qty, entry_date, entry_price, target_price,
                                 stop_price, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                              (p["id"], p["code"], p["name"], e["qty"], today, price, tgt, stop, "open",
                               self._ts(now), self._ts(now)))
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
                tgt, stop = self._levels(e["price"])
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

    def _expire(self, now: dt.datetime, mode: str, hold_days: int) -> None:
        for pos in self.positions("active"):
            if pos["status"] == "open" and self._held_days(pos["entry_date"], now.date()) >= hold_days:
                self._sell_all(now, pos, mode, "기간만료")

    def _finish_day(self, now: dt.datetime, today: str) -> None:
        for p in self.plans(today):
            if p["status"] == "ordered":
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
            self._refresh_targets(now, tstr, mode)
        # 3) 장중
        if OPEN <= t < CLOSE_AT:
            self.sync(now, mode)
            if t < STOP_UNTIL:
                self._check_stops(now, mode)
        # 4) 보유기간 만료
        if EXPIRE_AT <= t < EXPIRE_UNTIL and st.get("expire_done") != tstr:
            self._set_state(expire_done=tstr)
            self._expire(now, mode, self.rules()["hold"])
        # 5) 마무리
        if t >= CLOSE_AT and st.get("close_done") != tstr:
            self.sync(now, mode)
            self._set_state(close_done=tstr)
            self._finish_day(now, tstr)


