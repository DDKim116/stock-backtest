"""장중 실시간 감시: 고른 전략의 '지정가 대기' 종목과 '산 것으로 계산된' 종목을 키움 시세로 지켜보고 알린다.

주문은 하지 않는다 (알림만). 백테스트와 같은 규칙으로 판단한다.
- 대기: 현재가 ≤ 지정가 → '매수가 도달'. 그날 처음 본 가격이 이미 지정가 아래면 시가 체결로 보고 그 가격을 매수가로.
- 보유: 현재가 ≥ 매수가 × (1 + 목표%) → '목표 도달', ≤ 매수가 × (1 - 손절%) → '손절가 도달'
- 보유기간 마지막 날 15:10 → '오늘 종가 청산'
같은 알림은 하루 한 번만. 대기·보유 목록은 일봉 데이터(저녁 임시값 포함)로 계산한 '오늘 신호'와 같다.
"""
from __future__ import annotations

import datetime as dt
import threading
from typing import Callable

from .autotrade import is_trading_day

OPEN = dt.time(9, 0)
CLOSE = dt.time(15, 20)
LAST_DAY_AT = dt.time(15, 10)


class Watch:
    def __init__(self, store, kw, get_runner: Callable, notify: Callable[[str, str], None], parse_text: Callable):
        self.store, self.kw, self.get_runner, self.notify, self.parse_text = store, kw, get_runner, notify, parse_text
        self.lock = threading.Lock()
        self._scan: dict = {}  # (전략 id, 데이터 날짜, 전략 글) → 행
        self.prices: dict[str, float] = {}
        self.updated: str | None = None

    # ------------------------------------------------------------ 설정·상태
    def settings(self) -> dict:
        return {"strategy_ids": [], **self.store.get_setting("watch", {})}

    def set_settings(self, strategy_ids: list[int]) -> dict:
        self.store.set_setting("watch", {"strategy_ids": [int(x) for x in strategy_ids]})
        return self.settings()

    def _day(self, today: str) -> dict:
        st = self.store.get_setting("watch_state", {})
        if st.get("date") != today:
            st = {"date": today, "sent": [], "entry": {}, "first": {}}
        return st

    def _mode(self) -> str | None:
        v = self.kw.view()
        return "real" if v.get("real", {}).get("saved") else ("mock" if v.get("mock", {}).get("saved") else None)

    # ------------------------------------------------------------ 감시 목록
    def items(self) -> list[dict]:
        """고른 전략마다 대기·보유 종목 (일봉 기준)."""
        out = []
        if not self.settings()["strategy_ids"]:
            return out
        runner = self.get_runner("kr")
        as_of = str(runner.panel.dates.max())
        for sid in self.settings()["strategy_ids"]:
            strat = self.store.get(sid)
            if not strat:
                continue
            key = (sid, as_of, strat["text"])
            if key not in self._scan:
                spec = self.parse_text(strat["text"])
                rows = runner.scan(spec)["rows"]
                x = spec.exit
                self._scan = {k: v for k, v in self._scan.items() if k[0] != sid}
                self._scan[key] = (rows, x.target_pct, x.stop_pct, int(x.max_days))
            rows, tgt, stop, hold = self._scan[key]
            for r in rows:
                if r["kind"] not in ("pending", "holding"):
                    continue
                if r["kind"] == "pending" and r.get("limit") is None:
                    continue
                out.append({"sid": sid, "strategy": strat["name"], "code": r["code"], "name": r["name"],
                            "signal_date": r["signal_date"], "kind": r["kind"], "limit": r.get("limit"),
                            "left": r.get("left"), "entry": r.get("fill_price"), "fill_date": r.get("fill_date"),
                            "held": r.get("held"), "target_pct": tgt, "stop_pct": stop, "hold": hold,
                            "as_of": as_of})
        return out

    def view(self, now: dt.datetime) -> dict:
        st = self._day(now.date().isoformat())
        try:
            items = self.items()
        except Exception as e:  # noqa: BLE001
            return {"settings": self.settings(), "items": [], "error": str(e), "updated": self.updated}
        for it in items:
            k = f"{it['sid']}:{it['code']}:{it['signal_date']}"
            p = self.prices.get(it["code"])
            it["price"] = p
            if it["kind"] == "pending" and k in st["entry"]:
                it["kind"], it["entry"], it["touched_today"] = "holding", st["entry"][k], True
            if it["kind"] == "holding" and it["entry"]:
                e = it["entry"]
                it["target"] = e * (1 + it["target_pct"] / 100) if it["target_pct"] is not None else None
                it["stop"] = e * (1 - it["stop_pct"] / 100) if it["stop_pct"] is not None else None
                it["ret"] = (p / e - 1) * 100 if p else None
            elif it["kind"] == "pending" and p and it["limit"]:
                it["gap"] = (p / it["limit"] - 1) * 100  # 지정가까지 남은 %
            it["events"] = [x.split(":")[-1] for x in st["sent"] if x.startswith(k + ":")]
        return {"settings": self.settings(), "items": items, "updated": self.updated, "mode": self._mode()}

    # ------------------------------------------------------------ 주기 실행
    def tick(self, now: dt.datetime) -> None:
        with self.lock:
            self._tick(now)

    def _tick(self, now: dt.datetime) -> None:
        if not self.settings()["strategy_ids"] or not is_trading_day(now.date()):
            return
        t = now.time()
        if not (OPEN <= t < CLOSE):
            return
        mode = self._mode()
        if not mode:
            return
        items = self.items()
        if not items:
            return
        self.prices = self.kw.quotes(mode, sorted({it["code"] for it in items}))
        self.updated = now.strftime("%H:%M:%S")
        today = now.date().isoformat()
        st = self._day(today)
        sent = set(st["sent"])
        msgs: list[tuple[str, str]] = []

        def fire(k: str, kind: str, title: str, body: str):
            if f"{k}:{kind}" not in sent:
                sent.add(f"{k}:{kind}")
                msgs.append((title, body))

        for it in items:
            p = self.prices.get(it["code"])
            if not p:
                continue
            k = f"{it['sid']}:{it['code']}:{it['signal_date']}"
            first = st["first"].setdefault(k, p)  # 그날 처음 본 가격 ≈ 시가
            entry = it["entry"]
            if it["kind"] == "pending" and k not in st["entry"]:
                if p <= it["limit"]:
                    entry = first if first <= it["limit"] else it["limit"]
                    st["entry"][k] = entry
                    fire(k, "buy", f"매수가 도달 · {it['name']}",
                         f"{it['strategy']}\n지정가 {it['limit']:,.0f}원 · 현재 {p:,.0f}원 → {entry:,.0f}원에 산 것으로 봅니다")
                else:
                    continue
            elif it["kind"] == "pending":
                entry = st["entry"][k]
            if not entry:
                continue
            if it["target_pct"] is not None and p >= entry * (1 + it["target_pct"] / 100):
                fire(k, "target", f"목표 +{it['target_pct']:g}% 도달 · {it['name']}",
                     f"{it['strategy']}\n매수 {entry:,.0f}원 → 현재 {p:,.0f}원 ({(p / entry - 1) * 100:+.2f}%)")
            if it["stop_pct"] is not None and p <= entry * (1 - it["stop_pct"] / 100):
                fire(k, "stop", f"손절가 -{it['stop_pct']:g}% 도달 · {it['name']}",
                     f"{it['strategy']}\n매수 {entry:,.0f}원 → 현재 {p:,.0f}원 ({(p / entry - 1) * 100:+.2f}%)")
            # 보유기간 마지막 날 (체결일 다음부터 오늘까지 거래일 = 보유기간)
            if it["kind"] == "holding" and it["held"] is not None and t >= LAST_DAY_AT:
                if it["held"] + 1 >= it["hold"]:
                    fire(k, "last", f"보유기간 마지막 날 · {it['name']}",
                         f"{it['strategy']}\n백테스트 규칙으로는 오늘 종가에 정리합니다 (현재 {p:,.0f}원, {(p / entry - 1) * 100:+.2f}%)")
        st["sent"] = sorted(sent)
        self.store.set_setting("watch_state", st)
        for title, body in msgs:
            self.notify(title, body)
