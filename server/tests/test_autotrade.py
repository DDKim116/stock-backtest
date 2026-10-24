import datetime as dt
from types import SimpleNamespace

import numpy as np
import pytest

from sbt import autotrade
from sbt.api.store import Store
from sbt.broker import kiwoom
from sbt.engine.spec import parse_text

TEXT = """[신호]
수익률(5) <= -15
[매수]
다음날시가
[청산]
목표 = 10
손절 = 7
보유기간 = 5
[대상]
시장 = 코스피, 코스닥
"""


class FakeKw:
    round_tick = staticmethod(kiwoom.round_tick)

    def __init__(self):
        self.orders = {}  # no → dict
        self.fills = {}  # no → {qty, price}
        self.prices = {}
        self.n = 0
        self.cancelled = []

    def view(self):
        return {"mock": {"saved": True}, "real": {"saved": False}}

    def cash(self, mode):
        return {"orderable": 50_000_000}

    def _new(self, **o):
        self.n += 1
        no = str(self.n)
        self.orders[no] = o
        return no

    def buy_limit(self, mode, code, qty, price):
        assert mode == "mock"
        return self._new(side="buy", code=code, qty=qty, price=price)

    def sell_limit(self, mode, code, qty, price):
        return self._new(side="sell", kind="limit", code=code, qty=qty, price=price)

    def sell_market(self, mode, code, qty):
        return self._new(side="sell", kind="market", code=code, qty=qty)

    def cancel(self, mode, code, no):
        self.cancelled.append(no)
        return "c" + no

    def quotes(self, mode, codes):
        return {c: self.prices[c] for c in codes if c in self.prices}

    def executions(self, mode):
        return {no: {"qty": f["qty"], "price": f["price"], "left": self.orders[no]["qty"] - f["qty"],
                     "ordered": self.orders[no]["qty"]} for no, f in self.fills.items()}

    def fill(self, no, price, qty=None):
        self.fills[no] = {"qty": qty or self.orders[no]["qty"], "price": price}

    def find(self, **kw):
        return [no for no, o in self.orders.items() if all(o.get(k) == v for k, v in kw.items())]


class FakeRunner:
    def __init__(self):
        self.days = ["2026-10-22", "2026-10-23"]
        self.rows = {}

    @property
    def panel(self):
        return SimpleNamespace(dates=np.array(self.days, dtype="datetime64[D]"))

    def scan(self, spec, days):
        d = self.days[-1]
        return {"rows": self.rows.get(d, [])}


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "app.sqlite3")
    sid = store.save("급락 반등 v2b · 시험", TEXT)["id"]
    kw, runner, sent = FakeKw(), FakeRunner(), []
    a = autotrade.Auto(store, kw, lambda ds: runner, lambda t, b: sent.append((t, b)), parse_text)
    a.set_settings(enabled=True, strategy_id=sid)
    return a, kw, runner, sent


def T(day, hh, mm, ss=0):
    return dt.datetime(2026, 10, day, hh, mm, ss) if day >= 10 else dt.datetime(2026, 11, day, hh, mm, ss)


def test_calendar():
    assert autotrade.next_trading_day(dt.date(2026, 10, 23)) == dt.date(2026, 10, 26)
    assert autotrade.next_trading_day(dt.date(2026, 10, 8)) == dt.date(2026, 10, 12)  # 10/9 한글날
    assert kiwoom.round_tick(10_499, up=False) == 10_490 and kiwoom.round_tick(10_001, up=True) == 10_010
    assert kiwoom.round_tick(52_345, up=True) == 52_400 and kiwoom.round_tick(1_999.5, up=True) == 2_000


def test_week(env):
    a, kw, runner, sent = env
    rows = [  # 거래대금 작은 순으로 골라야 한다
        {"code": "000001", "name": "가", "signal_date": "2026-10-23", "close": 10_000, "value": 5e9},
        {"code": "000002", "name": "나", "signal_date": "2026-10-23", "close": 20_000, "value": 2e9},
        {"code": "000003", "name": "다", "signal_date": "2026-10-23", "close": 6_000_000, "value": 9e9},
        {"code": "000004", "name": "라", "signal_date": "2026-10-23", "close": 30_000, "value": 1e9},
    ]
    runner.rows["2026-10-23"] = rows
    a.set_settings(daily_max=3)

    # 금요일 저녁: 월요일 후보 (승인 모드)
    a.tick(T(23, 20, 15))
    plans = a.plans("2026-10-26")
    assert [p["code"] for p in plans] == ["000004", "000002", "000001"]
    assert all(p["status"] == "proposed" for p in plans) and "승인 필요" in sent[-1][0]
    p4, p2, p1 = plans
    assert p1["qty"] == 500 and p1["limit_price"] == 10_500  # 500만 / 1만, 종가 +5%
    a.tick(T(23, 20, 16))
    assert len(a.plans("2026-10-26")) == 3  # 같은 데이터로 다시 만들지 않는다

    a.approve(p1["id"], True, T(26, 8, 0))
    a.approve(p2["id"], True, T(26, 8, 0))
    with pytest.raises(ValueError):
        a.approve(p4["id"], True, T(26, 8, 46))  # 승인 시간 지남

    # 월요일 08:25 알림, 08:50 매수
    a.tick(T(26, 8, 26))
    assert "승인 대기 1종목" in sent[-1][0]
    a.tick(T(26, 8, 50))
    st = {p["code"]: p["status"] for p in a.plans("2026-10-26")}
    assert st == {"000001": "ordered", "000002": "ordered", "000004": "expired"}
    b1, b2 = kw.find(side="buy", code="000001")[0], kw.find(side="buy", code="000002")[0]

    # 09:01 체결 → 목표가 주문
    kw.fill(b1, 10_000)
    kw.fill(b2, 19_800)
    a.tick(T(26, 9, 1))
    pos = {p["code"]: p for p in a.positions("active")}
    assert pos["000001"]["target_price"] == 11_000 and pos["000001"]["stop_price"] == pytest.approx(9_300)
    t1 = kw.find(side="sell", kind="limit", code="000001")[0]
    assert kw.orders[t1]["qty"] == 500 and kw.orders[t1]["price"] == 11_000

    # 10:00 '가' 손절선 아래 → 목표가 취소, 시장가 매도
    kw.prices = {"000001": 9_250, "000002": 20_100}
    a.tick(T(26, 10, 0))
    assert t1 in kw.cancelled
    m1 = kw.find(side="sell", kind="market", code="000001")[0]
    kw.fill(m1, 9_240)
    a.tick(T(26, 10, 1))
    closed = [p for p in a.positions() if p["status"] == "closed"]
    assert closed[0]["exit_reason"] == "손절" and closed[0]["ret"] == pytest.approx(-7.6)
    assert any("손절" in t for t, _ in sent)

    # 장 마감 정리
    a.tick(T(26, 15, 32))
    assert len(a.positions("active")) == 1

    # 화~금: 매일 아침 목표가 주문을 다시 건다, 목표 안 닿음
    for d, prev in ((27, "2026-10-26"), (28, "2026-10-27"), (29, "2026-10-28"), (30, "2026-10-29")):
        runner.days.append(prev)
        a.tick(T(d, 8, 50))
        a.tick(T(d, 15, 21))  # 아직 만료 아님
        a.tick(T(d, 15, 32))
    n2 = kw.find(side="sell", kind="limit", code="000002")
    assert len(n2) == 5  # 월(체결 직후) + 화~금
    assert not kw.find(side="sell", kind="market", code="000002")

    # 다음 월요일(체결일 + 5거래일) 15:20 기간 만료 → 시장가
    runner.days.append("2026-10-30")
    a.tick(T(2, 8, 50))
    a.tick(T(2, 15, 21))
    m2 = kw.find(side="sell", kind="market", code="000002")
    assert len(m2) == 1
    kw.fill(m2[0], 20_500)
    a.tick(T(2, 15, 32))
    last = [p for p in a.positions() if p["code"] == "000002"][0]
    assert last["status"] == "closed" and last["exit_reason"] == "기간만료"
    v = a.view()
    assert v["stats"]["n"] == 2 and v["stats"]["win"] == 1


def test_target_and_disable(env):
    a, kw, runner, sent = env
    runner.rows["2026-10-23"] = [{"code": "000009", "name": "마", "signal_date": "2026-10-23", "close": 10_000, "value": 1e9}]
    a.set_settings(mode="auto")
    a.tick(T(23, 20, 15))
    assert a.plans("2026-10-26")[0]["status"] == "approved" and "자동 매수 예정" in sent[-1][0]
    a.tick(T(26, 8, 50))
    b = kw.find(side="buy")[0]
    kw.fill(b, 10_100)
    a.tick(T(26, 9, 1))
    t = kw.find(side="sell", kind="limit")[0]
    assert kw.orders[t]["price"] == 11_110 + 0  # 10,100 × 1.1 = 11,110 (호가 10원)
    kw.fill(t, 11_110)
    a.tick(T(26, 11, 0))
    pos = a.positions()[0]
    assert pos["status"] == "closed" and pos["exit_reason"] == "목표" and pos["ret"] == pytest.approx(10.0)

    # 전체 중지: 새 후보·매수 없음
    a.set_settings(enabled=False)
    runner.days.append("2026-10-26")
    runner.rows["2026-10-26"] = runner.rows["2026-10-23"]
    a.tick(T(26, 20, 15))
    assert a.plans("2026-10-27") == []


def test_real_orders_blocked():
    with pytest.raises(kiwoom.KiwoomError, match="실전 주문"):
        kiwoom.buy_limit("real", "005930", 1, 70000)
