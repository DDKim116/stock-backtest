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
