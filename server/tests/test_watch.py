import datetime as dt
from types import SimpleNamespace

import numpy as np

from sbt import watch
from sbt.api.store import Store
from sbt.engine.spec import parse_text

TEXT = """[신호]
등락률 >= 15
[매수]
지정가 = (시가 + 종가) / 2
유효기간 = 5
[청산]
목표 = 5
손절 = 2
보유기간 = 5
"""


class Kw:
    def __init__(self):
        self.px = {}

    def view(self):
        return {"real": {"saved": True}, "mock": {"saved": True}}

    def quotes(self, mode, codes):
        assert mode == "real"
        return {c: self.px[c] for c in codes if c in self.px}


class Runner:
    panel = SimpleNamespace(dates=np.array(["2026-10-23"], dtype="datetime64[D]"))

    def scan(self, spec, days=None):
        return {"rows": [
            {"code": "000001", "name": "가", "signal_date": "2026-10-23", "kind": "pending", "limit": 10_000, "left": 5},
            {"code": "000002", "name": "나", "signal_date": "2026-10-19", "kind": "holding", "fill_price": 20_000,
             "fill_date": "2026-10-20", "held": 3},
            {"code": "000003", "name": "다", "signal_date": "2026-10-16", "kind": "closed"},
        ]}


def test_watch(tmp_path):
    store = Store(tmp_path / "a.sqlite3")
    sid = store.save("중간값 매수", TEXT)["id"]
    kw, sent = Kw(), []
    w = watch.Watch(store, kw, lambda ds: Runner(), lambda t, b: sent.append((t, b)), parse_text)
    w.set_settings([sid])
    now = dt.datetime(2026, 10, 26, 9, 0, 20)
    kw.px = {"000001": 10_300, "000002": 20_500}
    w.tick(now)
    assert sent == []
    v = w.view(now)
    assert {i["code"]: i["kind"] for i in v["items"]} == {"000001": "pending", "000002": "holding"}
    assert round(v["items"][0]["gap"], 1) == 3.0

    kw.px = {"000001": 9_990, "000002": 21_050}  # 지정가 도달 + '나' 목표 +5%
    w.tick(now.replace(minute=30))
    titles = [t for t, _ in sent]
    assert any("매수가 도달 · 가" in t for t in titles) and any("목표 +5% 도달 · 나" in t for t in titles)
    assert "10,000원에 산 것으로" in sent[0][1]  # 처음 본 가격은 지정가 위였으므로 지정가 체결
    n = len(sent)
    w.tick(now.replace(minute=31))
    assert len(sent) == n  # 같은 알림은 하루 한 번

    kw.px = {"000001": 9_790, "000002": 21_000}  # '가' 손절 -2% (10,000 기준 9,800 이하)
    w.tick(now.replace(hour=10))
    assert any("손절가 -2% 도달 · 가" in t for t, _ in sent)
    v = w.view(now.replace(hour=10))
    a = [i for i in v["items"] if i["code"] == "000001"][0]
    assert a["kind"] == "holding" and a["entry"] == 10_000 and set(a["events"]) == {"buy", "stop"}

    w.tick(now.replace(hour=15, minute=11))  # '나' 는 체결 후 3일 + 오늘 = 4일 → 아직 마지막 날 아님
    assert not any("마지막 날" in t for t, _ in sent)
    w.tick(dt.datetime(2026, 10, 25, 10, 0))  # 일요일은 쉼
