"""저장한 전략 (SQLite)."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from pathlib import Path

_lock = threading.Lock()


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS strategies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                text TEXT NOT NULL,
                note TEXT DEFAULT '',
                last_summary TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS ai_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                kind TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER, output_tokens INTEGER, cache_write INTEGER, cache_read INTEGER,
                cost_usd REAL NOT NULL
            )""")
        self.db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        # 매매일지: 거래(종목 하나의 매매 묶음)와 체결(분할 매수·매도 각각)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS journal_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                market TEXT DEFAULT '',
                strategy_id INTEGER,
                strategy_name TEXT DEFAULT '',
                strategy_text TEXT DEFAULT '',
                signal_date TEXT,
                memo TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS journal_fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER NOT NULL REFERENCES journal_trades(id) ON DELETE CASCADE,
                side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
                date TEXT NOT NULL,
                price REAL NOT NULL,
                qty REAL NOT NULL,
                fee REAL,
                memo TEXT DEFAULT ''
            )""")
        self.db.commit()

    # ------------------------------------------------------------ 설정
    def get_setting(self, key: str, default=None):
        with _lock:
            r = self.db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def set_setting(self, key: str, value) -> None:
        with _lock:
            self.db.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, json.dumps(value)))
            self.db.commit()

    # ------------------------------------------------------------ AI 사용량
    def add_usage(self, kind: str, usage) -> None:
        with _lock:
            self.db.execute(
                "INSERT INTO ai_usage (ts, kind, model, input_tokens, output_tokens, cache_write, cache_read, cost_usd) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (dt.datetime.now().isoformat(timespec="seconds"), kind, usage.model, usage.input_tokens,
                 usage.output_tokens, usage.cache_write, usage.cache_read, usage.cost_usd))
            self.db.commit()

    def month_usage(self) -> dict:
        start = dt.date.today().replace(day=1).isoformat()
        with _lock:
            r = self.db.execute("SELECT COUNT(*) AS n, COALESCE(SUM(cost_usd), 0) AS usd FROM ai_usage WHERE ts >= ?",
                                (start,)).fetchone()
            avg = self.db.execute(
                "SELECT kind, AVG(cost_usd) AS usd FROM (SELECT * FROM ai_usage ORDER BY id DESC LIMIT 50) GROUP BY kind"
            ).fetchall()
        return {"count": r["n"], "usd": r["usd"], "avg_by_kind": {x["kind"]: x["usd"] for x in avg}}

    def list(self) -> list[dict]:
        with _lock:
            rows = self.db.execute("SELECT * FROM strategies ORDER BY updated_at DESC").fetchall()
        return [self._row(r) for r in rows]

    def get(self, sid: int) -> dict | None:
        with _lock:
            r = self.db.execute("SELECT * FROM strategies WHERE id = ?", (sid,)).fetchone()
        return self._row(r) if r else None

    def save(self, name: str, text: str, note: str = "", sid: int | None = None,
             last_summary: dict | None = None) -> dict:
        now = dt.datetime.now().isoformat(timespec="seconds")
        summ = json.dumps(last_summary, ensure_ascii=False) if last_summary is not None else None
        with _lock:
            if sid:
                self.db.execute(
                    "UPDATE strategies SET name=?, text=?, note=?, last_summary=COALESCE(?, last_summary), "
                    "updated_at=? WHERE id=?", (name, text, note, summ, now, sid))
            else:
                cur = self.db.execute(
                    "INSERT INTO strategies (name, text, note, last_summary, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)", (name, text, note, summ, now, now))
                sid = cur.lastrowid
            self.db.commit()
        return self.get(sid)

    def delete(self, sid: int) -> None:
        with _lock:
            self.db.execute("DELETE FROM strategies WHERE id = ?", (sid,))
            self.db.commit()

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["last_summary"] = json.loads(d["last_summary"]) if d.get("last_summary") else None
        return d

    # ------------------------------------------------------------ 매매일지
    def journal_list(self, dataset: str | None = None) -> list[dict]:
        with _lock:
            q = "SELECT * FROM journal_trades" + (" WHERE dataset = ?" if dataset else "") + " ORDER BY updated_at DESC"
            trades = [dict(r) for r in self.db.execute(q, (dataset,) if dataset else ()).fetchall()]
            fills = self.db.execute("SELECT * FROM journal_fills ORDER BY date, CASE side WHEN 'buy' THEN 0 ELSE 1 END, id"
                                    ).fetchall()
        by: dict[int, list] = {}
        for f in fills:
            by.setdefault(f["trade_id"], []).append(dict(f))
        for t in trades:
            t["fills"] = by.get(t["id"], [])
        return trades

    def journal_get(self, tid: int) -> dict | None:
        with _lock:
            r = self.db.execute("SELECT * FROM journal_trades WHERE id = ?", (tid,)).fetchone()
            if not r:
                return None
            t = dict(r)
            t["fills"] = [dict(f) for f in self.db.execute(
                "SELECT * FROM journal_fills WHERE trade_id = ? ORDER BY date, CASE side WHEN 'buy' THEN 0 ELSE 1 END, id",
                (tid,)).fetchall()]
        return t

    def journal_create(self, d: dict) -> int:
        now = dt.datetime.now().isoformat(timespec="seconds")
        with _lock:
            cur = self.db.execute(
                "INSERT INTO journal_trades (dataset, code, name, market, strategy_id, strategy_name, strategy_text, "
                "signal_date, memo, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (d["dataset"], d["code"], d["name"], d.get("market", ""), d.get("strategy_id"),
                 d.get("strategy_name", ""), d.get("strategy_text", ""), d.get("signal_date"), d.get("memo", ""),
                 now, now))
            self.db.commit()
            return cur.lastrowid

    def journal_update(self, tid: int, d: dict) -> None:
        keys = [k for k in ("memo", "strategy_id", "strategy_name", "strategy_text", "signal_date") if k in d]
        if not keys:
            return
        now = dt.datetime.now().isoformat(timespec="seconds")
        with _lock:
            self.db.execute(f"UPDATE journal_trades SET {', '.join(k + ' = ?' for k in keys)}, updated_at = ? WHERE id = ?",
                            [d[k] for k in keys] + [now, tid])
            self.db.commit()

    def journal_delete(self, tid: int) -> None:
        with _lock:
            self.db.execute("DELETE FROM journal_fills WHERE trade_id = ?", (tid,))
            self.db.execute("DELETE FROM journal_trades WHERE id = ?", (tid,))
            self.db.commit()

    def fill_add(self, tid: int, f: dict) -> int:
        now = dt.datetime.now().isoformat(timespec="seconds")
        with _lock:
            cur = self.db.execute(
                "INSERT INTO journal_fills (trade_id, side, date, price, qty, fee, memo) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (tid, f["side"], f["date"], f["price"], f["qty"], f.get("fee"), f.get("memo", "")))
            self.db.execute("UPDATE journal_trades SET updated_at = ? WHERE id = ?", (now, tid))
            self.db.commit()
            return cur.lastrowid

    def fill_get(self, fid: int) -> dict | None:
        with _lock:
            r = self.db.execute("SELECT * FROM journal_fills WHERE id = ?", (fid,)).fetchone()
        return dict(r) if r else None

    def fill_delete(self, fid: int) -> None:
        with _lock:
            self.db.execute("DELETE FROM journal_fills WHERE id = ?", (fid,))
            self.db.commit()
