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
        self.db.commit()

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
