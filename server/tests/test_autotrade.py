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
        self.manual = {}  # 키움 앱에서 손으로 판 수량 (음수)
        # strict: 실제 키움처럼 매도가능수량(잔고 - 걸린 매도 주문)을 보고, 취소는 바로 처리되지 않을 수 있다
        self.strict = False
        self.cancel_delay = 0  # strict: 취소가 처리되기 전 매도 시도가 몇 번 막히는지
        self.pending = {}  # 처리 대기 중인 취소: 주문번호 → 남은 횟수
        self.dead = set()  # 취소 처리된 주문 (남은 수량은 더 체결되지 않음)
        self.old_parser = False  # 예전 executions 처럼 취소된 주문을 '주문 - 미체결' = 전부 체결로 보여 줌
        self.list_open = False  # True: 미체결 조회(open_orders)에 체결 없는 주문도 보여 줌

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

    def held(self, code):
        return next((h["qty"] for h in self.balance("mock")["holdings"] if h["code"] == code), 0)

    def _check_sell(self, code, qty):
        if not self.strict:
            return
        reserved = sum(o["qty"] - self.fills.get(no, {}).get("qty", 0) for no, o in self.orders.items()
                       if o["side"] == "sell" and o["code"] == code and no not in self.dead)
        ok = qty <= self.held(code) - reserved
        for no in list(self.pending):  # 매도 시도마다 취소 처리가 한 걸음씩
            self.pending[no] -= 1
            if self.pending[no] <= 0:
                self.dead.add(no)
                del self.pending[no]
        if not ok:
            raise kiwoom.KiwoomError("[2000](800033:모의투자 매도가능수량이 부족합니다.)")

    def sell_limit(self, mode, code, qty, price):
        self._check_sell(code, qty)
        return self._new(side="sell", kind="limit", code=code, qty=qty, price=price)

    def sell_market(self, mode, code, qty):
        self._check_sell(code, qty)
        return self._new(side="sell", kind="market", code=code, qty=qty)

    def cancel(self, mode, code, no):
        self.cancelled.append(no)
        if self.strict:
            o = self.orders.get(no)
            if o is None or no in self.dead or self.fills.get(no, {}).get("qty", 0) >= o["qty"]:
                raise kiwoom.KiwoomError("[2000](취소가능수량이 없습니다)")
            if self.cancel_delay:
                self.pending[no] = self.cancel_delay
            else:
                self.dead.add(no)
        return "c" + no

    def quotes(self, mode, codes):
        return {c: self.prices[c] for c in codes if c in self.prices}

    def executions(self, mode):
        out = {}
        for no, f in self.fills.items():
            o, dead = self.orders[no], no in self.dead
            out[no] = {"qty": o["qty"] if dead and self.old_parser else f["qty"], "price": f["price"],
                       "left": 0 if dead else o["qty"] - f["qty"], "ordered": o["qty"], "code": o["code"], "side": o["side"],
                       "kind": "order"}
        return out

    def open_orders(self, mode):
        if not self.list_open:  # 미체결 조회가 안 되는 경우 (응답 모양이 다르면 kiwoom.open_orders 도 오류를 올린다)
            raise kiwoom.KiwoomError("미체결 조회 응답에 oso 가 없습니다")
        out = {}
        for no, o in self.orders.items():  # 살아 있는 주문 (일부 체결돼 남은 것 포함)
            f = self.fills.get(no, {}).get("qty", 0)
            if no not in self.dead and f < o["qty"]:
                out[no] = {"qty": f, "price": self.fills.get(no, {}).get("price", 0), "left": o["qty"] - f,
                           "ordered": o["qty"], "code": o["code"], "side": o["side"], "kind": "order"}
        return out

    def balance(self, mode):
        q = dict(self.manual)
        for no, f in self.fills.items():
            o = self.orders[no]
            q[o["code"]] = q.get(o["code"], 0) + (f["qty"] if o["side"] == "buy" else -f["qty"])
        return {"assets": 50_000_000, "pnl": 0, "holdings": [{"code": c, "qty": n} for c, n in q.items() if n > 0]}

    def fill(self, no, price, qty=None):
        self.fills[no] = {"qty": qty or self.orders[no]["qty"], "price": price}

    def find(self, **kw):
        return [no for no, o in self.orders.items() if all(o.get(k) == v for k, v in kw.items())]


class FakeRunner:
    def __init__(self):
        self.days = ["2026-10-22", "2026-10-23"]
        self.rows = {}
        self.lrows = {}  # 지정가(중간값) 전략용

    @property
    def panel(self):
        return SimpleNamespace(dates=np.array(self.days, dtype="datetime64[D]"))

    def scan(self, spec, days=None):
        d = self.days[-1]
        src = self.lrows if spec.entry.type == "limit" else self.rows
        return {"rows": src.get(d, [])}


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "app.sqlite3")
    sid = store.save("급락 반등 v2b · 시험", TEXT)["id"]
    kw, runner, sent = FakeKw(), FakeRunner(), []
    a = autotrade.Auto(store, kw, lambda ds: runner, lambda t, b: sent.append((t, b)), parse_text)
    a.retry_wait = 0  # 목표가 취소 뒤 다시 시도 간격 (시험에서는 기다리지 않음)
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
    dly = a.daily()  # 날마다 실현 손익 + 장 마감 계좌 금액
    assert sum(len(d["items"]) for d in dly["days"]) == 2
    assert all(d["pnl"] == sum(x["pnl"] for x in d["items"]) for d in dly["days"])
    assert any(e["date"] == "2026-11-02" for e in dly["equity"])


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


def test_manual_and_reconcile(env):
    a, kw, runner, sent = env
    runner.rows["2026-10-23"] = [
        {"code": "000011", "name": "갑", "signal_date": "2026-10-23", "close": 10_000, "value": 1e9},
        {"code": "000012", "name": "을", "signal_date": "2026-10-23", "close": 10_000, "value": 2e9},
    ]
    a.set_settings(mode="auto")
    a.tick(T(23, 20, 15))
    assert {k: v for k, v in a.view()["last_plan"].items() if k != "per"} == {"trade_date": "2026-10-26", "signal_date": "2026-10-23", "count": 2}
    a.tick(T(26, 8, 50))
    for no in kw.find(side="buy"):
        kw.fill(no, 10_000)
    a.tick(T(26, 9, 1))
    gap, eul = sorted(a.positions("active"), key=lambda p: p["code"])

    # 앱의 '지금 팔기'
    with pytest.raises(ValueError):
        a.manual_sell(gap["id"], T(26, 8, 0))  # 장 전
    a.manual_sell(gap["id"], T(26, 10, 0))
    m = kw.find(side="sell", kind="market", code="000011")[0]
    kw.fill(m, 10_200)
    a.tick(T(26, 10, 1))
    g = [p for p in a.positions() if p["code"] == "000011"][0]
    assert g["status"] == "closed" and g["exit_reason"] == "직접 매도" and g["ret"] == pytest.approx(2.0)

    # 키움 앱에서 '을'을 손으로 판 경우: 우리 주문이 아닌 매도 체결 + 잔고 0
    kw.manual["000012"] = -500
    no = kw._new(side="sell", kind="limit", code="000012", qty=500, price=9_900)
    kw.fill(no, 9_900)
    a.tick(T(26, 11, 0))  # 한 번 본 것으로는 정리하지 않는다
    assert [p["status"] for p in a.positions() if p["code"] == "000012"] == ["open"]
    a.tick(T(26, 11, 3))
    e = [p for p in a.positions() if p["code"] == "000012"][0]
    assert e["status"] == "closed" and e["exit_reason"] == "키움에서 직접 매도" and e["ret"] == pytest.approx(-1.0)
    v = a.view()
    assert v["stats"]["n"] == 0 and v["stats_manual"]["n"] == 2

    # 신호 없는 날: last_plan 은 0종목으로
    runner.days.append("2026-10-26")
    a.tick(T(26, 20, 15))
    assert a.view()["last_plan"]["count"] == 0 and a.view()["last_plan"]["trade_date"] == "2026-10-27"


MID = """[신호]
수익률(1) >= 15
[매수]
지정가 = (시가 + 종가) / 2
유효기간 = 3
[청산]
목표 = 5
손절 = 2
보유기간 = 5
[대상]
시장 = 코스피, 코스닥
"""


def test_two_strategies_shared_cash(env):
    a, kw, runner, sent = env
    mid = a.store.save("중간값 매수 · 시험", MID)["id"]
    x = a.set_strategy(mid)
    assert x["per_trade"] == 3_000_000 and x["daily_max"] == 5 and x["color"] != a.settings()["strategies"][0]["color"]
    a.set_settings(mode="auto")
    runner.rows["2026-10-23"] = [{"code": "000001", "name": "가", "signal_date": "2026-10-23", "close": 10_000, "value": 1e9}]
    runner.lrows["2026-10-23"] = [
        {"code": "000001", "name": "가", "signal_date": "2026-10-23", "close": 11_000, "limit": 10_000, "value": 1e9,
         "kind": "pending", "left": 3},
        {"code": "000021", "name": "중", "signal_date": "2026-10-23", "close": 23_000, "limit": 20_037, "value": 3e9,
         "kind": "pending", "left": 3},
    ]
    a.tick(T(23, 20, 15))
    plans = {p["code"]: p for p in a.plans("2026-10-26")}
    assert set(plans) == {"000001", "000021"}  # 같은 종목은 앞 전략에만
    m = plans["000021"]
    assert m["strategy_id"] == mid and m["entry_kind"] == "limit" and m["limit_price"] == 20_000  # 호가 내림
    assert m["qty"] == 150 and m["valid_until"] == "2026-10-28"
    assert a.view()["last_plan"]["per"] == {"급락 반등 v2b · 시험": 1, "중간값 매수 · 시험": 1}
    v = a.view()
    assert [s["midpoint"] for s in v["strategies"]] == [False, True]

    # 월: 둘 다 주문, '중'은 안 닿음 → 장 마감 뒤 다시 승인 상태
    a.tick(T(26, 8, 50))
    assert len(kw.find(side="buy")) == 2
    b1 = kw.find(side="buy", code="000001")[0]
    kw.fill(b1, 10_000)
    a.tick(T(26, 9, 1))
    a.tick(T(26, 15, 32))
    m = [p for p in a.plans() if p["code"] == "000021"][0] if hasattr(a, "plans") else None
    m = a._q("SELECT * FROM at_plans WHERE code = '000021'")[0]
    assert m["status"] == "approved" and "남은 2일" in m["msg"]

    # 화: 다시 주문 → 체결 → 중간값 전략 규칙(+5 / −2)으로 목표가
    runner.days.append("2026-10-26")
    a.tick(T(27, 8, 50))
    b2 = kw.find(side="buy", code="000021")
    assert len(b2) == 2 and kw.orders[b2[-1]]["price"] == 20_000
    kw.fill(b2[-1], 20_000)
    a.tick(T(27, 9, 5))
    pos = {p["code"]: p for p in a.positions("active")}
    assert pos["000021"]["strategy_id"] == mid and pos["000021"]["target_price"] == 21_000
    assert pos["000021"]["stop_price"] == pytest.approx(19_600)
    assert pos["000001"]["target_price"] == 11_000
    assert a._q("SELECT status FROM at_plans WHERE code = '000021'")[0]["status"] == "filled"


def test_limit_plan_waits_for_cash(env):
    a, kw, runner, sent = env
    mid = a.store.save("중간값 매수 · 시험", MID)["id"]
    a.remove_strategy(a.settings()["strategies"][0]["id"])
    a.set_strategy(mid, per_trade=3_000_000)
    a.set_settings(mode="auto")
    runner.lrows["2026-10-23"] = [{"code": "000021", "name": "중", "signal_date": "2026-10-23", "close": 23_000,
                                   "limit": 20_000, "value": 3e9, "kind": "pending", "left": 3}]
    a.tick(T(23, 20, 15))
    kw.cash = lambda mode: {"orderable": 1_000_000}
    a.tick(T(26, 8, 50))
    p = a._q("SELECT * FROM at_plans")[0]
    assert p["status"] == "approved" and "내일 다시" in p["msg"] and not kw.find(side="buy")
    a.approve(p["id"], False, T(27, 8, 0))  # 둘째 날에도 뺄 수 있다
    assert a._q("SELECT status FROM at_plans")[0]["status"] == "rejected"


def test_limit_picks_older_pending_and_added_later(env):
    """중간값: 며칠 전 신호도 아직 안 닿았으면 후보. 전략을 늦게 추가해도 바로 후보를 만든다."""
    a, kw, runner, sent = env
    runner.rows["2026-10-23"] = [{"code": "000001", "name": "가", "signal_date": "2026-10-23", "close": 10_000, "value": 1e9}]
    a.tick(T(23, 20, 15))  # v2b 만 있는 상태에서 후보를 이미 만듦
    mid = a.store.save("중간값 매수 · 시험", MID)["id"]
    runner.lrows["2026-10-23"] = [
        {"code": "000041", "name": "사흘전", "signal_date": "2026-10-20", "close": 30_000, "limit": 27_000, "value": 2e9,
         "kind": "pending", "left": 1},
        {"code": "000042", "name": "닿음", "signal_date": "2026-10-21", "close": 30_000, "limit": 27_000, "value": 1e9,
         "kind": "holding", "left": None},
        {"code": "000043", "name": "끝남", "signal_date": "2026-10-16", "close": 30_000, "limit": 27_000, "value": 1e9,
         "kind": "expired", "left": 0},
    ]
    a.set_strategy(mid)
    a.tick(T(23, 20, 20))  # 데이터가 같으면 다시 만들지 않는다 → API 가 only 로 부른다
    assert [p["code"] for p in a.plans("2026-10-26")] == ["000001"]
    a.make_plans(T(23, 20, 21), only={mid})
    ps = {p["code"]: p for p in a.plans("2026-10-26")}
    assert set(ps) == {"000001", "000041"}
    assert ps["000041"]["signal_date"] == "2026-10-20" and ps["000041"]["valid_until"] == "2026-10-26"  # 남은 1일
    lp = a.view()["last_plan"]
    assert lp["count"] == 2 and lp["per"]["중간값 매수 · 시험"] == 1
    a.make_plans(T(23, 20, 22), only={mid})  # 같은 신호는 두 번 넣지 않는다
    assert len(a.plans("2026-10-26")) == 2


def test_midpoint_color_lime(env):
    a, kw, runner, sent = env
    mid = a.store.save("중간값 매수 · 시험", MID)["id"]
    assert a.set_strategy(mid)["color"] == autotrade.LIME  # 새로 넣는 중간값 전략은 라임
    assert a.set_strategy(mid, color="#FF7AB8")["color"] == "#FF7AB8"
    with pytest.raises(ValueError):
        a.set_strategy(mid, color="red")


def test_midpoint_color_migrates(env):
    a, kw, runner, sent = env
    mid = a.store.save("중간값 매수 · 시험", MID)["id"]
    s = a.store.get_setting("autotrade", {})
    s["strategies"].append({"id": mid, "enabled": True, "per_trade": 3e6, "daily_max": 5, "rank": "value_asc",
                            "color": "#3CC4B4"})
    s["migrated"] = 0
    a.store.set_setting("autotrade", s)
    assert [x["color"] for x in a.settings()["strategies"]] == ["#A99CF5", autotrade.LIME]


def test_compare_actual_vs_expect(env, monkeypatch):
    a, kw, runner, sent = env
    sid = a.settings()["strategies"][0]["id"]
    rets = [10.0] * 20 + [-7.0] * 12  # 32건, 평균 +3.62% (비용 전)
    for k, r in enumerate(rets):
        a._x("""INSERT INTO at_positions (plan_id, code, name, qty, entry_date, entry_price, target_price, stop_price,
                status, created_at, updated_at, strategy_id, ret, exit_reason, exit_date, sold_qty, sold_amount)
                VALUES (NULL, ?, ?, 100, '2026-10-01', 10000, 11000, 9300, 'closed', 'x', 'x', ?, ?, ?, ?, 100, ?)""",
             (f"{k:06d}", "n", sid, r, "목표" if r > 0 else "손절", f"2026-{10 + k // 28}-{1 + k % 28:02d}", 10000 * 100 * (1 + r / 100)))
    a._x("""INSERT INTO at_positions (plan_id, code, name, qty, entry_date, entry_price, target_price, stop_price,
            status, created_at, updated_at, strategy_id, ret, exit_reason, exit_date, sold_qty, sold_amount)
            VALUES (NULL, '999999', 'm', 1, '2026-10-01', 1, 1, 1, 'closed', 'x', 'x', ?, 50.0, '직접 매도', '2026-10-02', 1, 1)""",
         (sid,))
    monkeypatch.setattr(a, "expectation", lambda s: {"n": 1000, "avg": 2.5, "std": 6.0, "win": 60.0, "since": "2021-01-01"})
    c = a.compare()[0]
    assert c["actual"]["n"] == 32 and c["actual"]["win"] == pytest.approx(62.5)  # 직접 매도는 뺀다
    assert c["actual"]["avg"] == pytest.approx((20 * 10 - 12 * 7) / 32 - 0.23, abs=0.01)
    assert c["band"][0] < 2.5 < c["band"][1] and c["verdict"] == "inside"
    assert c["actual"]["lose_streak"] == 12


def test_size_pct_of_account(env):
    a, kw, runner, sent = env
    sid = a.settings()["strategies"][0]["id"]
    kw.balance = lambda mode: {"holdings": [], "assets": 120_000_000}  # 계좌 1.2억
    a.set_strategy(sid, size_mode="pct", pct=10, max_per_trade=10_000_000)
    runner.rows["2026-10-23"] = [{"code": "000001", "name": "가", "signal_date": "2026-10-23", "close": 10_000, "value": 1e9}]
    a.tick(T(23, 20, 15))
    assert a.plans("2026-10-26")[0]["qty"] == 1_000  # 10% = 1,200만 → 상한 1,000만 / 1만원
    with pytest.raises(ValueError):
        a.set_strategy(sid, pct=80)
    # 계좌 금액을 못 읽으면 고정 금액(500만)으로
    kw.balance = lambda mode: (_ for _ in ()).throw(RuntimeError("x"))
    x = a.settings()["strategies"][0]
    assert a.amount(x, a._equity(a.settings(), T(23, 20, 16))) == 5_000_000


def test_disable_strategy_expires_pending(env):
    a, kw, runner, sent = env
    sid = a.settings()["strategies"][0]["id"]
    runner.rows["2026-10-23"] = [{"code": "000001", "name": "가", "signal_date": "2026-10-23", "close": 10_000, "value": 1e9}]
    a.tick(T(23, 20, 15))
    assert a.plans("2026-10-26")[0]["status"] == "proposed"
    a.set_strategy(sid, enabled=False)
    p = a.plans("2026-10-26")[0]
    assert p["status"] == "expired" and p["msg"] == "전략을 끔"


def test_held_days_when_data_is_late(env):
    a, kw, runner, sent = env
    # 데이터가 11/9 까지만 있어도(11/10 갱신 지연) 11/11 은 11/4 매수 뒤 5거래일째
    runner.days = ["2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09"]
    assert a._held_days("2026-11-04", dt.date(2026, 11, 11)) == 5
    runner.days.append("2026-11-10")
    assert a._held_days("2026-11-04", dt.date(2026, 11, 11)) == 5
    assert a._held_days("2026-11-04", dt.date(2026, 11, 10)) == 4


def test_overdue_expiry_sold_next_morning(env):
    a, kw, runner, sent = env
    runner.days = ["2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09", "2026-11-10", "2026-11-11"]
    now = dt.datetime(2026, 11, 12, 9, 1)
    pid = a._x("""INSERT INTO at_positions (code, name, qty, entry_date, entry_price, target_price, stop_price, status,
                  created_at, updated_at, hold_days) VALUES ('057030','YBM넷',10,'2026-11-04',5000,5500,4650,'open',?,?,5)""",
               (now.isoformat(), now.isoformat()))
    kw.manual = {"057030": 10, "000002": 10}  # 키움 잔고
    a._overdue(now, "mock")
    pos = [p for p in a.positions() if p["id"] == pid][0]
    assert pos["status"] == "closing" and pos["exit_reason"] == "기간만료"
    assert any(o["side"] == "sell" and o["kind"] == "market" and o["code"] == "057030" for o in kw.orders.values())
    # 아직 만료 전(5거래일째 당일)인 종목은 건드리지 않는다
    pid2 = a._x("""INSERT INTO at_positions (code, name, qty, entry_date, entry_price, target_price, stop_price, status,
                   created_at, updated_at, hold_days) VALUES ('000002','나',10,'2026-11-05',5000,5500,4650,'open',?,?,5)""",
                (now.isoformat(), now.isoformat()))
    a._overdue(now, "mock")
    assert [p for p in a.positions() if p["id"] == pid2][0]["status"] == "open"


def test_expiry_runs_even_if_execution_check_fails(env, monkeypatch):
    a, kw, runner, sent = env
    runner.days = ["2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09", "2026-11-10"]
    t0 = dt.datetime(2026, 11, 11, 15, 21)
    pid = a._x("""INSERT INTO at_positions (code, name, qty, entry_date, entry_price, target_price, stop_price, status,
                  created_at, updated_at, hold_days) VALUES ('298000','효성화학',10,'2026-11-04',5000,5500,4650,'open',?,?,5)""",
               (t0.isoformat(), t0.isoformat()))
    kw.manual = {"298000": 10}

    def boom(mode):
        raise kiwoom.KiwoomError("1700: 허용된 요청 개수를 초과하였습니다")

    monkeypatch.setattr(kw, "executions", boom)
    a.tick(t0)
    pos = [p for p in a.positions() if p["id"] == pid][0]
    assert pos["status"] == "closing" and pos["exit_reason"] == "기간만료"
    assert "체결 확인" in a._state()["last_error"]


def test_expiry_at_1510_retries_until_order_goes_in(env, monkeypatch):
    a, kw, runner, sent = env
    runner.days = ["2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09", "2026-11-10"]
    t0 = dt.datetime(2026, 11, 11, 15, 10, 5)
    pid = a._x("""INSERT INTO at_positions (code, name, qty, entry_date, entry_price, target_price, stop_price, status,
                  created_at, updated_at, hold_days) VALUES ('298000','효성화학',10,'2026-11-04',5000,5500,4650,'open',?,?,5)""",
               (t0.isoformat(), t0.isoformat()))
    kw.manual = {"298000": 10}
    real = kw.sell_market
    fails = {"n": 2}

    def flaky(mode, code, qty):
        if fails["n"] > 0:
            fails["n"] -= 1
            raise kiwoom.KiwoomError("매도가능수량이 부족합니다")
        return real(mode, code, qty)

    monkeypatch.setattr(kw, "sell_market", flaky)
    status = lambda: [p for p in a.positions() if p["id"] == pid][0]["status"]  # noqa: E731
    a.tick(t0)
    assert status() == "open" and a._state().get("expire_done") != "2026-11-11"  # 실패 → 끝난 것으로 치지 않는다
    alerts = lambda: [t for t, _ in sent if "매도 실패" in t or "매도 막힘" in t]  # noqa: E731
    assert len(alerts()) == 1 and "매도 막힘" in alerts()[0]  # '매도가능수량 부족'은 바로 막힘 알림
    a.tick(t0 + dt.timedelta(seconds=20))
    assert status() == "open" and len(alerts()) == 1  # 알림은 한 번만
    a.tick(t0 + dt.timedelta(seconds=40))
    assert status() == "closing" and a._state()["expire_done"] == "2026-11-11"
    assert any("기간만료 확인" in r["text"] for r in a.view()["log"])


def test_partial_exit_fill_recorded_and_rest_sold_next_day(env):
    """모의투자는 시장가도 일부만 체결될 수 있다: 판 만큼 기록하고 나머지는 다음 거래일 장 시작에."""
    a, kw, runner, sent = env
    runner.days = ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]
    t0 = dt.datetime(2026, 11, 16, 14, 30, 5)
    pid = a._x("""INSERT INTO at_positions (code, name, qty, entry_date, entry_price, target_price, stop_price, status,
                  created_at, updated_at, hold_days) VALUES ('024880','케이피에프',1633,'2026-11-09',2948,3245,2742,'open',?,?,5)""",
               (t0.isoformat(), t0.isoformat()))
    kw.manual = {"024880": 1633}  # 키움 잔고에 있는 수량 (가짜 계좌)
    get = lambda: [p for p in a.positions() if p["id"] == pid][0]  # noqa: E731
    a.tick(t0)
    assert get()["status"] == "closing"
    no = kw.find(side="sell", kind="market", code="024880")[0]
    kw.fill(no, 2900, qty=1000)  # 마감까지 1000주만
    a.tick(dt.datetime(2026, 11, 16, 15, 31, 5))
    p = get()
    assert p["status"] == "open" and p["sold_qty"] == 1000 and p["sold_amount"] == 2_900_000
    assert any("1000주만 체결" in r["text"] for r in a.view()["log"])
    # 다음 거래일 장 시작: 남은 633주만 판다
    runner.days.append("2026-11-16")
    a.tick(dt.datetime(2026, 11, 17, 9, 0, 30))
    no2 = [n for n in kw.find(side="sell", kind="market", code="024880") if n != no][0]
    assert kw.orders[no2]["qty"] == 633
    kw.fill(no2, 2950)
    a.tick(dt.datetime(2026, 11, 17, 9, 1, 0))
    p = get()
    assert p["status"] == "closed" and p["sold_qty"] == 1633 and p["exit_reason"] == "기간만료"
    assert abs(p["sold_amount"] - (2_900_000 + 633 * 2950)) < 1


def _pos(a, code, name, entry, qty=100, price=10_000, now=None):
    now = now or dt.datetime(2026, 11, 16, 9, 0)
    return a._x("""INSERT INTO at_positions (code, name, qty, entry_date, entry_price, target_price, stop_price, status,
                   created_at, updated_at, hold_days) VALUES (?,?,?,?,?,?,?,'open',?,?,5)""",
                (code, name, qty, entry, price, int(price * 1.1), price * 0.93, now.isoformat(), now.isoformat()))


def test_order_cap_skips_big_orders(env):
    a, kw, runner, sent = env
    a.set_settings(order_cap=1_000_000)
    runner.rows["2026-10-23"] = [{"code": "000001", "name": "가", "signal_date": "2026-10-23", "close": 10_000, "value": 5e9}]
    a.tick(T(23, 17, 0))
    a.approve(a.plans("2026-10-26")[0]["id"], True, T(23, 18, 0))
    a.tick(T(26, 8, 50))
    p = a.plans("2026-10-26")[0]
    assert p["status"] == "skipped" and "상한" in p["msg"] and not kw.find(side="buy")


def test_risky_stock_skipped_at_plan(env):
    a, kw, runner, sent = env
    kw.stock_risk = lambda mode, code: "관리종목" if code == "000002" else None
    runner.rows["2026-10-23"] = [
        {"code": "000001", "name": "가", "signal_date": "2026-10-23", "close": 10_000, "value": 5e9},
        {"code": "000002", "name": "나", "signal_date": "2026-10-23", "close": 10_000, "value": 2e9}]
    a.tick(T(23, 17, 0))
    ps = {p["code"]: p for p in a.plans("2026-10-26")}
    assert ps["000002"]["status"] == "skipped" and "관리종목" in ps["000002"]["msg"]
    assert ps["000001"]["status"] == "proposed"


def test_daily_loss_pauses_next_day_buy_only(env):
    a, kw, runner, sent = env
    a._x("INSERT INTO at_equity (date, assets, pnl, ts) VALUES ('2026-11-13', 50000000, 0, 'x')")
    kw.balance = lambda mode: {"assets": 45_000_000, "pnl": -5e6, "holdings": []}  # -10% (기본 한도 8%)
    a.tick(dt.datetime(2026, 11, 16, 10, 0))
    st = a._state()
    assert st["halted"] == "2026-11-16" and a.settings()["enabled"]  # 끄지 않는다
    assert any("손실 한도" in t for t, _ in sent)
    # 다음 거래일 후보는 하루 쉼
    runner.days = ["2026-11-13", "2026-11-16"]
    runner.rows["2026-11-16"] = [{"code": "000001", "name": "가", "signal_date": "2026-11-16", "close": 10_000, "value": 5e9}]
    a.tick(dt.datetime(2026, 11, 16, 17, 0))
    a.approve(a.plans("2026-11-17")[0]["id"], True, dt.datetime(2026, 11, 16, 18, 0))
    a.tick(dt.datetime(2026, 11, 17, 8, 50))
    p = a.plans("2026-11-17")[0]
    assert p["status"] == "skipped" and "하루 쉼" in p["msg"] and not kw.find(side="buy")
    # 그다음 거래일은 저절로 다시 산다
    runner.days = ["2026-11-16", "2026-11-17"]
    runner.rows["2026-11-17"] = [{"code": "000002", "name": "나", "signal_date": "2026-11-17", "close": 10_000, "value": 5e9}]
    a.tick(dt.datetime(2026, 11, 17, 17, 0))
    a.approve(a.plans("2026-11-18")[0]["id"], True, dt.datetime(2026, 11, 17, 18, 0))
    a.tick(dt.datetime(2026, 11, 18, 8, 50))
    assert kw.find(side="buy")


def test_small_daily_loss_does_not_pause(env):
    a, kw, runner, sent = env
    a._x("INSERT INTO at_equity (date, assets, pnl, ts) VALUES ('2026-11-13', 50000000, 0, 'x')")
    kw.balance = lambda mode: {"assets": 48_000_000, "pnl": -2e6, "holdings": []}  # -4%
    a.tick(dt.datetime(2026, 11, 16, 10, 0))
    assert not a._state().get("halted")


def test_migrate_loss_limit_and_reenable(env):
    a, kw, runner, sent = env
    a.store.set_setting("autotrade", {**a.settings(), "daily_loss_pct": 3.0, "enabled": False, "migrated": 1})
    a._set_state(halted="2026-11-16", halted_why="전날 마감 4,930만 → 지금 4,770만 (-3.2%)")
    s = a.settings()
    assert s["daily_loss_pct"] == 8.0 and s["enabled"] and not a._state().get("halted")


def test_emergency_cancels_and_sells(env):
    a, kw, runner, sent = env
    runner.rows["2026-10-23"] = [{"code": "000001", "name": "가", "signal_date": "2026-10-23", "close": 10_000, "value": 5e9}]
    a.tick(T(23, 17, 0))
    a.approve(a.plans("2026-10-26")[0]["id"], True, T(23, 18, 0))
    a.tick(T(26, 8, 50))
    assert a.plans("2026-10-26")[0]["status"] == "ordered"
    _pos(a, "000009", "다", "2026-10-22", now=T(26, 9, 30))
    kw.manual = {"000009": 100}
    r = a.emergency(T(26, 9, 30), sell_all=True)
    assert r["cancelled"] == 1 and r["sold"] == 1 and kw.cancelled
    assert a.plans("2026-10-26")[0]["status"] == "cancelled" and not a.settings()["enabled"]
    assert any(o["side"] == "sell" and o["kind"] == "market" and o["code"] == "000009" for o in kw.orders.values())


def test_stop_records_reference_price(env):
    a, kw, runner, sent = env
    runner.days = ["2026-11-12", "2026-11-13"]
    pid = _pos(a, "000007", "라", "2026-11-13", qty=10, price=10_000)
    kw.manual = {"000007": 10}
    kw.prices["000007"] = 9_200
    a.tick(dt.datetime(2026, 11, 16, 10, 0))
    no = kw.find(side="sell", kind="market", code="000007")[0]
    kw.fill(no, 9_154)
    a.tick(dt.datetime(2026, 11, 16, 10, 1))
    d = a.daily()
    it = d["days"][-1]["items"][0]
    assert it["slip"] == -0.5 and d["slip"]["n"] == 1


def test_replan_when_official_data_arrives(env):
    a, kw, runner, sent = env
    stamp = ["night"]
    a._data_stamp = lambda: stamp[0]
    a.tick(T(23, 21, 44))  # 저녁 임시 데이터: 신호 없음
    assert not a.plans("2026-10-26")
    runner.rows["2026-10-23"] = [{"code": "000001", "name": "가", "signal_date": "2026-10-23", "close": 10_000, "value": 5e9}]
    a.tick(T(26, 8, 21))  # 같은 데이터 → 다시 안 봄
    assert not a.plans("2026-10-26")
    stamp[0] = "morning"  # 아침 공식 데이터로 바뀜
    a.tick(T(26, 8, 22))
    ps = a.plans("2026-10-26")
    assert len(ps) == 1 and ps[0]["status"] == "proposed"
    assert any("추가" in r["text"] for r in a._q("SELECT text FROM at_log"))
    a.tick(T(26, 8, 23))  # 또 바뀌어도 같은 종목은 다시 안 넣음
    stamp[0] = "again"
    a.tick(T(26, 8, 24))
    assert len(a.plans("2026-10-26")) == 1


def test_plan_now_after_deadline_refused_and_stale_expired(env):
    a, kw, runner, sent = env
    runner.rows["2026-10-23"] = [{"code": "000001", "name": "가", "signal_date": "2026-10-23", "close": 10_000, "value": 5e9}]
    with pytest.raises(ValueError, match="승인 시간"):
        a.make_plans(T(26, 9, 16), force=True)
    # 예전 방식으로 늦게 만들어진 후보가 남아 있으면 정리
    a.make_plans(T(26, 8, 0), force=True)
    a._upd("at_plans", a.plans("2026-10-26")[0]["id"], T(26, 9, 16), status="proposed")
    a._set_state(buy_done="2026-10-26")  # 08:50 매수는 이미 지나간 상태에서 늦게 생긴 후보
    a.tick(T(26, 9, 30))
    p = a.plans("2026-10-26")[0]
    assert p["status"] == "expired" and "승인 시간" in p["msg"]


def test_view_shows_no_candidates_instead_of_old_list(env):
    a, kw, runner, sent = env
    runner.rows["2026-10-23"] = [{"code": "000001", "name": "가", "signal_date": "2026-10-23", "close": 10_000, "value": 5e9}]
    a.tick(T(23, 17, 0))
    assert a.view("2026-10-23")["plan_date"] == "2026-10-26"
    runner.days = ["2026-10-23", "2026-10-26"]  # 다음 날 데이터: 신호 없음
    a.tick(T(26, 17, 0))
    v = a.view("2026-10-26")
    assert v["plan_date"] == "2026-10-27" and not [p for p in v["plans"] if p["trade_date"] == "2026-10-26"]


def test_manual_sell_retries_after_cancel(env):
    a, kw, runner, sent = env
    a.retry_wait = 0
    runner.rows["2026-10-23"] = [{"code": "000001", "name": "가", "signal_date": "2026-10-23", "close": 10_000, "value": 5e9}]
    a.tick(T(23, 17, 0))
    a.approve(a.plans("2026-10-26")[0]["id"], True, T(23, 18, 0))
    a.tick(T(26, 8, 50))
    kw.fill(kw.find(side="buy")[0], 10_000)
    a.tick(T(26, 9, 1))
    pos = a.positions("active")[0]
    assert pos["target_order"]
    real = kw.sell_market
    calls = []

    def flaky(mode, code, qty):
        calls.append(qty)
        if len(calls) == 1:
            raise RuntimeError("[2000](800033:모의투자 매도가능수량이 부족합니다.)")
        return real(mode, code, qty)

    kw.sell_market = flaky
    a.manual_sell(pos["id"], T(26, 9, 39))
    assert len(calls) == 2 and a.positions("active")[0]["status"] == "closing"


# ---------------------------------------------------------------- 실전 대비: 결과 모르는 주문 · 닫기 전 잔고 확인 · 잔고 불일치
def _bought(a, kw, runner, close=17_480, fill=17_900, day=29):
    """10/{day} 08:50 매수 → 체결 → 목표가 주문까지. (close 17,480 → 500만원이면 286주)"""
    runner.days = [f"2026-10-{day - 2:02d}", f"2026-10-{day - 1:02d}"]
    runner.rows[f"2026-10-{day - 1:02d}"] = [{"code": "200710", "name": "에이디테크놀로지", "signal_date": f"2026-10-{day - 1:02d}",
                                             "close": close, "value": 5e9}]
    a.tick(T(day - 1, 17, 0))
    a.approve(a.plans(f"2026-10-{day:02d}")[0]["id"], True, T(day - 1, 18, 0))
    a.tick(T(day, 8, 50))
    b = kw.find(side="buy")[0]
    kw.fill(b, fill)
    a.tick(T(day, 9, 1))
    return a.positions("active")[0]


def _logs(a):
    return [r["text"] for r in a._q("SELECT text FROM at_log ORDER BY id")]


def test_1029_manual_sell_after_partial_target_full_story(env):
    """10/29 에이디테크놀로지 재현: 286주 보유 · 목표가 50주 체결 · 지금 팔기 → 취소 → 첫 매도 '매도가능수량 부족'
    → 다시 성공 → 기록은 목표가 50주 + 직접 매도 236주, 키움 잔고 0."""
    a, kw, runner, sent = env
    kw.strict, kw.cancel_delay = True, 1  # 취소는 매도 시도 한 번 뒤에야 처리된다 (실제 키움처럼)
    pos = _bought(a, kw, runner)
    assert pos["qty"] == 286 and pos["entry_price"] == 17_900
    tno, tp = pos["target_order"], pos["target_price"]
    assert kw.orders[tno]["qty"] == 286
    kw.fill(tno, tp, qty=50)  # 목표가 50주만 체결
    a.tick(T(29, 9, 30))
    assert a.positions("active")[0]["target_filled"] == 50
    a.manual_sell(pos["id"], T(29, 9, 39))
    p = a.positions("active")[0]
    assert p["status"] == "closing" and p["sold_qty"] == 50 and p["target_order"] is None
    markets = kw.find(side="sell", kind="market")
    assert len(markets) == 1 and kw.orders[markets[0]]["qty"] == 236  # 첫 시도는 거절, 다시 236주
    assert tno in kw.dead
    kw.fill(markets[0], 17_000)
    a.tick(T(29, 9, 45))
    p = [x for x in a.positions() if x["id"] == pos["id"]][0]
    assert p["status"] == "closed" and p["exit_reason"] == "직접 매도" and p["sold_qty"] == 286
    assert p["sold_amount"] == pytest.approx(50 * tp + 236 * 17_000)
    assert p["ret"] == pytest.approx(((50 * tp + 236 * 17_000) / (17_900 * 286) - 1) * 100)
    assert kw.held("200710") == 0
    assert any("목표가로 50주 먼저 팔림" in t for t in _logs(a))
    a.tick(T(29, 9, 48))  # 잔고 확인: 남은 주식·불일치 없음
    a.tick(T(29, 9, 51))
    assert not a.view()["untracked"] and not any("불일치" in t for t, _ in sent)


def test_1029_old_parser_does_not_close_while_shares_remain(env):
    """체결로 보였지만 키움 잔고가 그만큼 줄지 않았으면 닫지 않고, 실제로 줄어든 만큼만 기록해 '확인 필요'로 둔다
    (예전 파서의 '취소 = 전부 체결' 오인 + 키움 앱에서 직접 취소). 다시 팔거나 목표가를 다시 걸지 않는다."""
    a, kw, runner, sent = env
    kw.strict, kw.old_parser = True, True
    pos = _bought(a, kw, runner)
    tno, tp = pos["target_order"], pos["target_price"]
    kw.fill(tno, tp, qty=50)
    a.tick(T(29, 9, 30))
    kw.dead.add(tno)  # 사용자가 키움 앱에서 목표가 주문을 취소 → 예전 파서는 286주 전부 체결로 보여 줌
    a.tick(T(29, 9, 40))
    a.tick(T(29, 9, 40, 40))
    assert a.positions("active")[0]["status"] == "open"  # 잔고 반영이 늦을 수 있어 90초는 기다린다
    a.tick(T(29, 9, 41, 30))
    p = a.positions("active")[0]
    assert p["status"] == "check" and p["sold_qty"] == 50 and p["sold_amount"] == pytest.approx(50 * tp)
    assert "50주만 줄었" in p["check_msg"] and any("확인 필요" in t for t, _ in sent)
    assert kw.find(side="sell", kind="limit") == [tno] and not kw.find(side="sell", kind="market")
    a.tick(T(29, 9, 45))
    a.tick(T(29, 10, 30))  # 확인 필요 동안 자동 매매가 손대지 않는다 (알림도 하루 한 번)
    assert kw.find(side="sell", kind="limit") == [tno] and sum("확인 필요" in t for t, _ in sent) == 1
    a.resolve(pos["id"], "resume", T(29, 10, 31))  # 키움 앱에서 236주 남은 것을 확인 → 다시 관리
    p = a.positions("active")[0]
    assert p["status"] == "open" and p["qty"] - p["sold_qty"] == 236
    a.tick(T(29, 10, 32))
    new = [n for n in kw.find(side="sell", kind="limit") if n != tno]
    assert len(new) == 1 and kw.orders[new[0]]["qty"] == 236  # 다시 관리하면 목표가를 다시 건다


def test_close_waits_one_check_for_balance_lag(env):
    """잔고 반영이 한 번 늦은 것뿐이면 다음 확인에서 정상으로 닫는다."""
    a, kw, runner, sent = env
    pos = _bought(a, kw, runner)
    real = kw.balance
    lag = {"on": True}
    kw.balance = lambda mode: ({"assets": 5e7, "holdings": [{"code": "200710", "qty": 286}]} if lag["on"] else real(mode))
    kw.fill(pos["target_order"], pos["target_price"])
    a.tick(T(29, 10, 0))
    assert a.positions("active")[0]["status"] == "open"
    lag["on"] = False
    a.tick(T(29, 10, 0, 20))
    p = [x for x in a.positions() if x["id"] == pos["id"]][0]
    assert p["status"] == "closed" and p["exit_reason"] == "목표" and p["sold_qty"] == 286
    assert not any("남아 있음" in t for t in _logs(a))


def test_buy_uncertain_found_in_executions(env):
    a, kw, runner, sent = env
    runner.rows["2026-10-23"] = [{"code": "000001", "name": "가", "signal_date": "2026-10-23", "close": 10_000, "value": 5e9}]
    a.tick(T(23, 17, 0))
    a.approve(a.plans("2026-10-26")[0]["id"], True, T(23, 18, 0))
    real = kw.buy_limit

    def timeout_after_accept(mode, code, qty, price):  # 키움은 받았는데 응답이 안 옴
        real(mode, code, qty, price)
        raise kiwoom.KiwoomUncertain("키움 응답을 받지 못했습니다 (결과 모름): Read timed out")

    kw.buy_limit = timeout_after_accept
    a.tick(T(26, 8, 50))
    p = a.plans("2026-10-26")[0]
    assert p["status"] == "unknown" and not p["order_no"] and any("확인 중" in t for t, _ in sent)
    a.tick(T(26, 8, 55))
    a.tick(T(26, 9, 0, 30))
    assert len(kw.find(side="buy")) == 1  # 다시 내지 않는다
    kw.fill(kw.find(side="buy")[0], 10_000)
    a.tick(T(26, 9, 1))
    p = a.plans("2026-10-26")[0]
    assert p["status"] == "filled" and p["order_no"] == kw.find(side="buy")[0]
    pos = a.positions("active")[0]
    assert pos["qty"] == 500 and pos["target_order"]
    assert len(kw.find(side="buy")) == 1


def test_buy_uncertain_never_sent_fails_only_at_close(env):
    a, kw, runner, sent = env
    runner.rows["2026-10-23"] = [{"code": "000001", "name": "가", "signal_date": "2026-10-23", "close": 10_000, "value": 5e9}]
    a.tick(T(23, 17, 0))
    a.approve(a.plans("2026-10-26")[0]["id"], True, T(23, 18, 0))

    def lost(mode, code, qty, price):
        raise kiwoom.KiwoomUncertain("키움 응답 오류 502: 키움 서버 일시 오류")

    kw.buy_limit = lost
    a.tick(T(26, 8, 50))
    for hh, mm in ((9, 1), (11, 0), (15, 0)):
        a.tick(T(26, hh, mm))
        assert a.plans("2026-10-26")[0]["status"] == "unknown"  # 장중에는 실패로 단정하지 않는다
    a.tick(T(26, 15, 32))
    p = a.plans("2026-10-26")[0]
    assert p["status"] == "failed" and "들어가지 않은" in p["msg"] and not a.positions()
    assert not kw.find(side="buy")


def test_buy_uncertain_adopted_from_balance_at_close(env):
    """체결 내역엔 안 보였지만 키움 잔고에 들어와 있으면 마감에 포지션으로 받는다 (규칙 없이 방치하지 않게)."""
    a, kw, runner, sent = env
    runner.rows["2026-10-23"] = [{"code": "000001", "name": "가", "signal_date": "2026-10-23", "close": 10_000, "value": 5e9}]
    a.tick(T(23, 17, 0))
    a.approve(a.plans("2026-10-26")[0]["id"], True, T(23, 18, 0))
    kw.buy_limit = lambda *x: (_ for _ in ()).throw(kiwoom.KiwoomUncertain("Read timed out"))
    a.tick(T(26, 8, 50))
    real = kw.balance
    kw.balance = lambda mode: {**real(mode), "holdings": [{"code": "000001", "name": "가", "qty": 500, "avg_price": 10_100}]}
    a.tick(T(26, 15, 32))
    p = a.plans("2026-10-26")[0]
    assert p["status"] == "filled" and "잔고가 500주 늘어" in p["msg"]
    pos = a.positions("active")[0]
    assert pos["qty"] == 500 and pos["entry_price"] == 10_100 and pos["status"] == "open"


def test_limit_plan_uncertain_retried_next_day_when_not_sent(env):
    a, kw, runner, sent = env
    mid = a.store.save("중간값 매수 · 시험", MID)["id"]
    a.remove_strategy(a.settings()["strategies"][0]["id"])
    a.set_strategy(mid, per_trade=3_000_000)
    a.set_settings(mode="auto")
    runner.lrows["2026-10-23"] = [{"code": "000021", "name": "중", "signal_date": "2026-10-23", "close": 23_000,
                                   "limit": 20_000, "value": 3e9, "kind": "pending", "left": 3}]
    a.tick(T(23, 20, 15))
    real = kw.buy_limit
    kw.buy_limit = lambda *x: (_ for _ in ()).throw(kiwoom.KiwoomUncertain("Read timed out"))
    a.tick(T(26, 8, 50))
    a.tick(T(26, 15, 32))
    p = a._q("SELECT * FROM at_plans")[0]
    assert p["status"] == "approved" and "내일 다시" in p["msg"]
    kw.buy_limit = real
    runner.days.append("2026-10-26")
    a.tick(T(27, 8, 50))
    assert len(kw.find(side="buy")) == 1 and a._q("SELECT status FROM at_plans")[0]["status"] == "ordered"


def test_sell_uncertain_found_in_executions(env):
    a, kw, runner, sent = env
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    real = kw.sell_market

    def timeout_after_accept(mode, code, qty):
        real(mode, code, qty)
        raise kiwoom.KiwoomUncertain("키움 응답을 받지 못했습니다 (결과 모름): Read timed out")

    kw.sell_market = timeout_after_accept
    a.manual_sell(pos["id"], T(29, 10, 0))  # 결과 모름이어도 '못 냄' 오류는 아니다
    p = a.positions("active")[0]
    assert p["status"] == "closing" and p["unsure"] == "exit" and p["exit_order"] is None
    m = kw.find(side="sell", kind="market")
    assert len(m) == 1
    kw.fill(m[0], 10_300, qty=200)  # 체결이 생기면 체결 내역에 보인다
    a.tick(T(29, 10, 0, 20))
    p = a.positions("active")[0]
    assert p["status"] == "closing" and p["exit_order"] == m[0] and not p["unsure"]
    kw.fill(m[0], 10_300)
    a.tick(T(29, 10, 1))
    p = [x for x in a.positions() if x["id"] == pos["id"]][0]
    assert p["status"] == "closed" and p["exit_reason"] == "직접 매도" and p["ret"] == pytest.approx(3.0)
    assert len(kw.find(side="sell", kind="market")) == 1


def test_sell_uncertain_never_resent_check_after_10min_then_carry(env):
    """결과 모르는 매도는 다시 내지 않는다. 10분 동안 체결도 잔고 변화도 없으면 '확인 필요',
    마감에 잔고가 그대로면 (주문은 사라졌다) 다음 거래일 장 시작에 같은 사유로 이어서 판다."""
    a, kw, runner, sent = env
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    kw.prices["200710"] = 9_000  # 손절선 아래
    kw.sell_market = lambda *x: (_ for _ in ()).throw(kiwoom.KiwoomUncertain("키움 응답 오류 504: 키움 서버 일시 오류"))
    a.tick(T(29, 10, 0))
    p = a.positions("active")[0]
    assert p["status"] == "closing" and p["unsure"] == "exit" and p["exit_reason"] == "손절" and p["base_qty"] == 500
    real = FakeKw.sell_market.__get__(kw)
    kw.sell_market = real
    for mm in (2, 5, 9):
        a.tick(T(29, 10, mm))
    assert not kw.find(side="sell", kind="market")  # 다시 내지 않는다
    a.tick(T(29, 10, 10, 20))
    p = a.positions("active")[0]
    assert p["status"] == "check" and "10분" in p["check_msg"] and any("확인 필요" in t for t, _ in sent)
    a.tick(T(29, 15, 32))  # 마감: 잔고가 기준값 그대로 → 안 팔림 확정 → 내일 이어서
    p = a.positions("active")[0]
    assert p["status"] == "open" and p["carry"] == "손절" and not p["unsure"] and p["sold_qty"] == 0
    runner.days.append("2026-10-29")
    kw.prices["200710"] = 9_800  # 값이 올라도 전날 시작한 손절은 마저 판다
    a.tick(T(30, 9, 0, 30))
    m = kw.find(side="sell", kind="market")
    assert len(m) == 1 and kw.orders[m[0]]["qty"] == 500
    assert a.positions("active")[0]["status"] == "closing" and a.positions("active")[0]["exit_reason"] == "손절"


def test_unknown_sell_not_resent_even_with_user_shares(env):
    """[돌돌이 D1] 같은 종목 개인 보유분(500주)이 있으면 키움이 두 번째 매도를 막지 못한다 → 절대 다시 내지 않는다."""
    a, kw, runner, sent = env
    kw.strict = True
    kw.manual = {"200710": 500}
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    real = kw.sell_market
    hidden = []

    def accepted_but_silent(mode, code, qty):
        hidden.append(real(mode, code, qty))
        kw.sell_market = real
        raise kiwoom.KiwoomUncertain("Read timed out")

    kw.sell_market = accepted_but_silent
    kw.list_open = True  # 개인 보유분이 있으면 목표가 취소가 '확인'돼야 판다 (미체결 조회로 확인)
    orig_open = kw.open_orders
    kw.open_orders = lambda mode: {k: v for k, v in orig_open(mode).items() if k not in hidden}  # 접수된 매도는 안 보인다고 치자
    a.manual_sell(pos["id"], T(29, 10, 0))
    kw.open_orders = lambda mode: (_ for _ in ()).throw(kiwoom.KiwoomError("미체결 조회 안 됨"))
    for t in (T(29, 10, 0, 30), T(29, 10, 1, 40), T(29, 10, 5)):
        a.tick(t)
    assert len(kw.find(side="sell", kind="market")) == 1
    a.tick(T(29, 10, 10, 30))
    assert a.positions("active")[0]["status"] == "check"
    kw.fill(hidden[0], 10_000)  # 늦게 다 체결
    a.tick(T(29, 10, 11))
    p = [x for x in a.positions() if x["id"] == pos["id"]][0]
    assert p["status"] == "closed" and p["sold_qty"] == 500 and kw.held("200710") == 500  # 개인 보유분은 그대로


def test_target_uncertain_found_in_executions(env):
    a, kw, runner, sent = env
    real = kw.sell_limit

    def timeout_after_accept(mode, code, qty, price):
        real(mode, code, qty, price)
        raise kiwoom.KiwoomUncertain("Read timed out")

    kw.sell_limit = timeout_after_accept
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    assert pos["unsure"] == "target" and pos["target_order"] is None
    kw.sell_limit = real
    t = kw.find(side="sell", kind="limit")
    assert len(t) == 1
    kw.fill(t[0], 11_000, qty=100)  # 일부 체결되며 체결 내역에 보인다
    a.tick(T(29, 9, 2))
    p = a.positions("active")[0]
    assert p["target_filled"] == 100
    assert p["target_order"] == t[0] and not p["unsure"] and len(kw.find(side="sell", kind="limit")) == 1
    a.tick(T(29, 9, 30))  # 목표가를 또 걸지 않는다
    assert len(kw.find(side="sell", kind="limit")) == 1


def test_untracked_and_extra_holdings_alert_once_a_day(env):
    a, kw, runner, sent = env
    _pos(a, "000009", "다", "2026-10-22", qty=100, now=T(26, 9, 0))
    kw.manual = {"000777": 30, "000009": 150}  # 키움에만 있는 종목 · 앱 기록(100주)보다 많은 종목
    a.tick(T(26, 9, 31))
    assert not any("불일치" in t for t, _ in sent)  # 한 번 본 것으로는 알리지 않는다
    a.tick(T(26, 9, 33, 30))
    alerts = [t for t, _ in sent if "불일치" in t]
    assert len(alerts) == 1 and "2종목" in alerts[0]
    u = {r["code"]: r for r in a.view()["untracked"]}
    assert u["000777"]["kind"] == "untracked" and u["000777"]["qty"] == 30
    assert u["000009"]["kind"] == "extra" and u["000009"]["qty"] == 50 and u["000009"]["held"] == 150
    assert any("키움에만 있는 종목" in t for t in _logs(a)) and any("앱 기록보다 많음" in t for t in _logs(a))
    a.tick(T(26, 9, 36))
    a.tick(T(26, 11, 0))
    assert len([t for t, _ in sent if "불일치" in t]) == 1  # 같은 날은 한 번만
    kw.manual = {"000009": 100}
    a.tick(T(26, 11, 3))
    assert a.view()["untracked"] == []


def _grow_setup(a, kw, runner):
    runner.rows["2026-10-23"] = [{"code": "000001", "name": "가", "signal_date": "2026-10-23", "close": 10_000, "value": 5e9}]
    a.tick(T(23, 17, 0))
    a.approve(a.plans("2026-10-26")[0]["id"], True, T(23, 18, 0))
    a.tick(T(26, 8, 50))
    b = kw.find(side="buy")[0]
    kw.fill(b, 10_000, qty=200)
    a.tick(T(26, 9, 1))
    pos = a.positions("active")[0]
    assert pos["qty"] == 200 and a.plans("2026-10-26")[0]["status"] == "partial"
    t1 = pos["target_order"]
    kw.fill(t1, pos["target_price"], qty=30)
    a.tick(T(26, 9, 2))
    kw.fill(b, 10_000, qty=500)  # 나머지 300주 체결
    return b, t1


def test_partial_buy_grows_position_and_keeps_target_fills(env):
    """매수가 나눠 체결: 그사이 목표가로 판 수량을 잃지 않고, 옛 목표가 취소가 확인되면 새 수량으로 다시 건다."""
    a, kw, runner, sent = env
    kw.strict = True  # 취소는 바로 처리됨
    b, t1 = _grow_setup(a, kw, runner)
    a.tick(T(26, 9, 3))
    p = a.positions("active")[0]
    assert p["qty"] == 500 and p["sold_qty"] == 30 and p["target_order"] not in (None, t1) and not p["old_target"]
    assert kw.orders[p["target_order"]]["qty"] == 470 and t1 in kw.dead
    assert a.plans("2026-10-26")[0]["status"] == "filled"


def test_partial_buy_grow_waits_for_cancel_confirmation(env):
    """[돌돌이 6·7] 옛 목표가 취소가 확인 안 되면 판 수량에 합치지 않고 옛 주문을 계속 따라간다 (그 뒤 체결도 반영).
    확인되면 합치고 새 목표가를 건다."""
    a, kw, runner, sent = env
    kw.strict, kw.cancel_delay = True, 5
    b, t1 = _grow_setup(a, kw, runner)
    a.tick(T(26, 9, 3))
    p = a.positions("active")[0]
    assert p["qty"] == 500 and p["sold_qty"] == 0 and p["old_target"] == t1 and p["target_filled"] == 30
    assert kw.find(side="sell", kind="limit") == [t1]  # 새 목표가는 아직
    kw.fill(t1, p["target_price"], qty=80)  # 취소 처리 전에 50주 더 팔림
    a.tick(T(26, 9, 4))
    assert a.positions("active")[0]["target_filled"] == 80
    kw.pending.clear()
    kw.dead.add(t1)  # 이제 취소 처리됨
    a.tick(T(26, 9, 5))
    p = a.positions("active")[0]
    assert p["sold_qty"] == 80 and not p["old_target"] and kw.orders[p["target_order"]]["qty"] == 420


def test_stop_cancels_rest_of_partial_buy(env):
    a, kw, runner, sent = env
    runner.rows["2026-10-23"] = [{"code": "000001", "name": "가", "signal_date": "2026-10-23", "close": 10_000, "value": 5e9}]
    a.tick(T(23, 17, 0))
    a.approve(a.plans("2026-10-26")[0]["id"], True, T(23, 18, 0))
    a.tick(T(26, 8, 50))
    b = kw.find(side="buy")[0]
    kw.fill(b, 10_000, qty=100)
    a.tick(T(26, 9, 1))
    kw.prices["000001"] = 9_000
    a.tick(T(26, 9, 5))
    assert b in kw.cancelled and "남은 매수 주문 취소" in a.plans("2026-10-26")[0]["msg"]
    assert a.positions("active")[0]["status"] == "closing"


def test_more_buy_fills_after_close_open_new_position(env):
    """목표가로 다 판 뒤 남은 매수 주문이 더 체결되면 닫힌 기록을 건드리지 않고 새 포지션으로."""
    a, kw, runner, sent = env
    runner.rows["2026-10-23"] = [{"code": "000001", "name": "가", "signal_date": "2026-10-23", "close": 10_000, "value": 5e9}]
    a.tick(T(23, 17, 0))
    a.approve(a.plans("2026-10-26")[0]["id"], True, T(23, 18, 0))
    a.tick(T(26, 8, 50))
    b = kw.find(side="buy")[0]
    kw.fill(b, 10_000, qty=100)
    a.tick(T(26, 9, 1))
    first = a.positions("active")[0]
    kw.fill(first["target_order"], first["target_price"])
    a.tick(T(26, 9, 2))
    closed = [x for x in a.positions() if x["id"] == first["id"]][0]
    assert closed["status"] == "closed" and closed["qty"] == 100 and closed["sold_qty"] == 100
    kw.fill(b, 10_000, qty=500)
    a.tick(T(26, 9, 3))
    closed2 = [x for x in a.positions() if x["id"] == first["id"]][0]
    assert closed2 == closed  # 닫힌 기록은 그대로
    new = a.positions("active")
    assert len(new) == 1 and new[0]["qty"] == 400 and new[0]["entry_price"] == 10_000 and new[0]["target_order"]


def test_finish_day_retries_when_executions_fail(env):
    """15:31 체결 조회가 안 되면 '미체결'로 적지 않고 다음 확인 때 다시 (산 종목을 다음 날 또 사지 않게)."""
    a, kw, runner, sent = env
    mid = a.store.save("중간값 매수 · 시험", MID)["id"]
    a.remove_strategy(a.settings()["strategies"][0]["id"])
    a.set_strategy(mid, per_trade=3_000_000)
    a.set_settings(mode="auto")
    runner.lrows["2026-10-23"] = [{"code": "000021", "name": "중", "signal_date": "2026-10-23", "close": 23_000,
                                   "limit": 20_000, "value": 3e9, "kind": "pending", "left": 3}]
    a.tick(T(23, 20, 15))
    a.tick(T(26, 8, 50))
    b = kw.find(side="buy")[0]
    real = kw.executions
    kw.executions = lambda mode: (_ for _ in ()).throw(kiwoom.KiwoomError("키움 응답 오류 502"))
    kw.fill(b, 20_000)  # 15:2x 에 체결됐는데 마감 때 체결 조회가 안 됨
    a.tick(T(26, 15, 31, 5))
    assert a._q("SELECT status FROM at_plans")[0]["status"] == "ordered" and a._state().get("close_done") != "2026-10-26"
    kw.executions = real
    a.tick(T(26, 15, 31, 25))
    assert a._q("SELECT status FROM at_plans")[0]["status"] == "filled" and a._state()["close_done"] == "2026-10-26"
    assert a.positions("active")[0]["qty"] == 150
    runner.days.append("2026-10-26")
    a.tick(T(27, 8, 50))
    assert len(kw.find(side="buy")) == 1  # 다음 날 다시 사지 않는다


def test_buy_step_retried_when_cash_lookup_fails(env):
    a, kw, runner, sent = env
    runner.rows["2026-10-23"] = [{"code": "000001", "name": "가", "signal_date": "2026-10-23", "close": 10_000, "value": 5e9}]
    a.tick(T(23, 17, 0))
    a.approve(a.plans("2026-10-26")[0]["id"], True, T(23, 18, 0))
    real = kw.cash
    kw.cash = lambda mode: (_ for _ in ()).throw(kiwoom.KiwoomError("키움 응답 오류 502"))
    a.tick(T(26, 8, 50))
    assert not kw.find(side="buy") and a.plans("2026-10-26")[0]["status"] == "approved"
    kw.cash = real
    a.tick(T(26, 8, 50, 20))
    a.tick(T(26, 8, 50, 40))
    assert len(kw.find(side="buy")) == 1 and a.plans("2026-10-26")[0]["status"] == "ordered"


def test_emergency_keeps_unknown_buy_and_reports(env):
    a, kw, runner, sent = env
    runner.rows["2026-10-23"] = [{"code": "000001", "name": "가", "signal_date": "2026-10-23", "close": 10_000, "value": 5e9}]
    a.tick(T(23, 17, 0))
    a.approve(a.plans("2026-10-26")[0]["id"], True, T(23, 18, 0))
    kw.buy_limit = lambda *x: (_ for _ in ()).throw(kiwoom.KiwoomUncertain("Read timed out"))
    a.tick(T(26, 8, 50))
    r = a.emergency(T(26, 9, 10))
    assert r["unsure"] == 1 and a.plans("2026-10-26")[0]["status"] == "unknown"
    assert any("결과 모르는 매수 주문" in b for _, b in sent)


def test_unknown_target_found_in_open_orders_lets_stop_sell(env):
    """목표가 주문 결과를 모르는데 실제로는 걸려 있으면(체결 없음) 미체결 조회로 찾아 취소하고 손절 매도를 낸다."""
    a, kw, runner, sent = env
    kw.strict, kw.list_open = True, True
    real = kw.sell_limit

    def timeout_after_accept(mode, code, qty, price):
        real(mode, code, qty, price)
        raise kiwoom.KiwoomUncertain("Read timed out")

    kw.sell_limit = timeout_after_accept
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    kw.sell_limit = real
    t = kw.find(side="sell", kind="limit")[0]
    kw.prices["200710"] = 9_000
    kw.list_open = False  # 체결 확인에서는 못 찾았다고 치자 (손절이 직접 찾아야 함)
    a.tick(T(29, 9, 1, 20))
    assert a.positions("active")[0]["unsure"] == "target"
    kw.list_open = True
    a._sell_all(T(29, 9, 2), a.positions("active")[0], "mock", "손절", ref=9_000)
    p = a.positions("active")[0]
    assert t in kw.cancelled and p["status"] == "closing" and not p["unsure"]
    assert kw.orders[p["exit_order"]]["qty"] == 500


def test_unknown_buy_found_in_open_orders_before_fill(env):
    a, kw, runner, sent = env
    kw.list_open = True
    runner.rows["2026-10-23"] = [{"code": "000001", "name": "가", "signal_date": "2026-10-23", "close": 10_000, "value": 5e9}]
    a.tick(T(23, 17, 0))
    a.approve(a.plans("2026-10-26")[0]["id"], True, T(23, 18, 0))
    real = kw.buy_limit

    def timeout_after_accept(mode, code, qty, price):
        real(mode, code, qty, price)
        raise kiwoom.KiwoomUncertain("Read timed out")

    kw.buy_limit = timeout_after_accept
    a.tick(T(26, 8, 50))
    a.tick(T(26, 9, 0, 30))
    p = a.plans("2026-10-26")[0]
    assert p["status"] == "ordered" and p["order_no"] == kw.find(side="buy")[0]


def test_balance_read_once_per_tick(env):
    """잔고(계좌 TR, 초당 1회 제한)는 한 번의 확인 안에서 잔고 확인·닫기 전 확인·손실 한도가 같이 써도 한 번만 읽는다."""
    a, kw, runner, sent = env
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    a._x("INSERT OR REPLACE INTO at_equity (date, assets, pnl, ts) VALUES ('2026-10-28', 50000000, 0, 'x')")
    real, n = kw.balance, {"n": 0}

    def counted(mode):
        n["n"] += 1
        return real(mode)

    kw.balance = counted
    kw.fill(pos["target_order"], pos["target_price"])
    a.tick(T(29, 10, 0))  # 닫기 전 잔고 확인 + 잔고 확인(2분마다) + 손실 한도(5분마다)가 한 번에
    assert n["n"] == 1
    assert [x for x in a.positions() if x["id"] == pos["id"]][0]["status"] == "closed"


# ---------------------------------------------------------------- 2차: 돌돌이 재현을 '고쳐졌으면 통과'로
def _silent_accept(kw, name):
    """키움은 받았는데 응답이 안 오는 주문 (한 번만)."""
    real = getattr(kw, name)
    hidden = []

    def f(*args):
        hidden.append(real(*args))
        setattr(kw, name, real)
        raise kiwoom.KiwoomUncertain("Read timed out")

    setattr(kw, name, f)
    return hidden


def test_hidden_target_blocking_stop_alerts_at_once(env):
    """[돌돌이 D2] 결과 모르는 목표가 주문이 숨어 있어 손절이 '매도가능수량 부족'으로 막히면 바로 전용 알림.
    사용자가 키움 앱에서 그 주문을 취소하면 다음 확인에서 손절이 나간다."""
    a, kw, runner, sent = env
    kw.strict = True
    hidden = _silent_accept(kw, "sell_limit")
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    assert pos["unsure"] == "target"
    kw.prices["200710"] = 9_000
    a.tick(T(29, 9, 5))
    assert not kw.find(side="sell", kind="market")
    blocked = [(t, b) for t, b in sent if "매도 막힘" in t]
    assert len(blocked) == 1 and "미체결 목표가" in blocked[0][1]
    a.tick(T(29, 9, 6))
    assert len([t for t, _ in sent if "매도 막힘" in t]) == 1  # 하루 한 번
    kw.dead.add(hidden[0])  # 사용자가 키움 앱에서 취소
    a.tick(T(29, 9, 7))
    m = kw.find(side="sell", kind="market")
    assert len(m) == 1 and a.positions("active")[0]["status"] == "closing"


def test_hidden_target_with_user_shares_does_not_sell(env):
    """같은 종목 개인 보유분이 있으면 키움이 막아 주지 못한다 → 숨은 목표가가 있을 수 있을 때는 팔지 않고 알린다."""
    a, kw, runner, sent = env
    kw.strict = True
    kw.manual = {"200710": 300}
    _silent_accept(kw, "sell_limit")
    _bought(a, kw, runner, close=10_000, fill=10_000)
    kw.prices["200710"] = 9_000
    a.tick(T(29, 9, 5))
    assert not kw.find(side="sell", kind="market") and kw.held("200710") == 800
    assert any("매도 멈춤" in t for t, _ in sent)


def test_settle_with_user_shares_does_not_close(env):
    """[돌돌이 D3] 체결로 보였지만 실제로 안 팔렸으면, 같은 종목 개인 보유분이 있어도 닫지 않는다 (기준값 비교)."""
    a, kw, runner, sent = env
    kw.strict, kw.old_parser = True, True
    kw.manual = {"200710": 10}
    pos = _bought(a, kw, runner)
    tno = pos["target_order"]
    kw.fills[tno] = {"qty": 0, "price": pos["target_price"]}
    kw.dead.add(tno)  # 체결 0인데 예전 파서는 '전부 체결'로 보여 줌
    a.tick(T(29, 9, 40))
    a.tick(T(29, 9, 41, 30))
    p = [x for x in a.positions() if x["id"] == pos["id"]][0]
    assert p["status"] == "check" and p["sold_qty"] == 0 and kw.held("200710") == 296


def test_cancel_row_not_matched_as_exit(env):
    """[돌돌이 D4] 체결 내역의 취소 주문 줄('매도취소', 같은 수량)을 결과 모르는 매도 주문으로 짝짓지 않는다."""
    a, kw, runner, sent = env
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    cancel_rows = {}
    real_cancel = kw.cancel

    def cancel_silent(mode, code, no):
        real_cancel(mode, code, no)
        kw.dead.add(no)
        kw.n += 1
        cancel_rows[str(kw.n)] = {"qty": 0, "price": 0, "left": 0, "ordered": kw.orders[no]["qty"], "code": code,
                                  "side": "sell", "kind": "cancel"}
        raise kiwoom.KiwoomUncertain("Read timed out")

    kw.cancel = cancel_silent
    hidden = _silent_accept(kw, "sell_market")
    real_ex = kw.executions
    kw.executions = lambda mode: {**cancel_rows, **real_ex(mode)}
    a.manual_sell(pos["id"], T(29, 10, 0))
    kw.fill(hidden[0], 10_200)
    a.tick(T(29, 10, 0, 20))
    p = [x for x in a.positions() if x["id"] == pos["id"]][0]
    assert p["status"] == "closed" and p["exit_reason"] == "직접 매도" and p["exit_price"] == 10_200
    assert p["exit_order"] == hidden[0] and p["exit_order"] not in cancel_rows


def test_cancel_unknown_then_target_fills_counted(env):
    """[돌돌이 D5] 취소 '결과 모름'(실제로 안 됨) + 매도 '결과 모름'(실제로 안 나감): 옛 목표가 주문을 계속 따라가
    그 뒤 목표가 체결을 반영해 '목표'로 닫는다 (다음 날 '키움에서 직접 매도'로 잘못 닫히지 않게)."""
    a, kw, runner, sent = env
    kw.strict = True
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    tno, tp = pos["target_order"], pos["target_price"]
    kw.cancel = lambda *x: (_ for _ in ()).throw(kiwoom.KiwoomUncertain("Read timed out"))
    real_sm = kw.sell_market
    kw.sell_market = lambda *x: (_ for _ in ()).throw(kiwoom.KiwoomUncertain("Read timed out"))
    kw.prices["200710"] = 9_000
    a.tick(T(29, 10, 0))
    p = a.positions("active")[0]
    assert p["status"] == "closing" and p["unsure"] == "exit" and p["old_target"] == tno and p["sold_qty"] == 0
    kw.sell_market = real_sm
    kw.prices["200710"] = 11_000
    kw.fill(tno, tp)  # 목표가 주문이 살아 있어 전부 체결
    a.tick(T(29, 10, 2))
    p = [x for x in a.positions() if x["id"] == pos["id"]][0]
    assert p["status"] == "closed" and p["exit_reason"] == "목표" and p["sold_qty"] == 500
    assert p["sold_amount"] == pytest.approx(500 * tp) and kw.held("200710") == 0
    assert not kw.find(side="sell", kind="market")


def test_unknown_buy_does_not_adopt_user_shares(env):
    """[돌돌이 D6] 결과 모르는 매수(실제로 안 나감) + 같은 종목 개인 보유 300주 → 주문 전 기준값(300)보다 늘지 않았으니 받지 않는다."""
    a, kw, runner, sent = env
    runner.rows["2026-10-23"] = [{"code": "000001", "name": "가", "signal_date": "2026-10-23", "close": 10_000, "value": 5e9}]
    a.tick(T(23, 17, 0))
    a.approve(a.plans("2026-10-26")[0]["id"], True, T(23, 18, 0))
    kw.buy_limit = lambda *x: (_ for _ in ()).throw(kiwoom.KiwoomUncertain("응답 오류 502"))
    kw.manual = {"000001": 300}
    a.tick(T(26, 8, 50))
    assert a.plans("2026-10-26")[0]["base_qty"] == 300
    a.tick(T(26, 15, 32))
    assert not a.positions("active") and a.plans("2026-10-26")[0]["status"] == "failed"
    runner.days.append("2026-10-26")
    a.tick(T(27, 8, 50))
    assert not kw.find(side="sell")


def test_unknown_buy_without_base_is_only_reported(env):
    """주문 전 잔고를 못 읽어 기준값이 없으면 마감에 받지 않고 '확인 필요'로 알리기만."""
    a, kw, runner, sent = env
    runner.rows["2026-10-23"] = [{"code": "000001", "name": "가", "signal_date": "2026-10-23", "close": 10_000, "value": 5e9}]
    a.tick(T(23, 17, 0))
    a.approve(a.plans("2026-10-26")[0]["id"], True, T(23, 18, 0))
    kw.buy_limit = lambda *x: (_ for _ in ()).throw(kiwoom.KiwoomUncertain("Read timed out"))
    real = kw.balance
    kw.balance = lambda mode: (_ for _ in ()).throw(kiwoom.KiwoomError("키움 응답 오류 502"))
    a.tick(T(26, 8, 50))
    kw.balance = real
    kw.manual = {"000001": 500}
    a.tick(T(26, 15, 32))
    p = a.plans("2026-10-26")[0]
    assert p["status"] == "check" and not a.positions("active") and any("확인 필요" in t for t, _ in sent)


def test_finish_giveup_judges_by_balance(env):
    """[돌돌이 D7] 23시까지 체결 조회가 안 되면 잔고 기준값으로 판정: 잔고가 다 줄었으면 원래 사유(손절)로 닫는다."""
    a, kw, runner, sent = env
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    kw.prices["200710"] = 9_000
    a.tick(T(29, 10, 0))
    m = kw.find(side="sell", kind="market")[0]
    kw.executions = lambda mode: (_ for _ in ()).throw(kiwoom.KiwoomError("키움 응답 오류 502"))
    kw.fill(m, 9_100)
    a.tick(T(29, 15, 32))
    assert a.positions("active") and a._state().get("close_done") != "2026-10-29"
    a.tick(T(29, 23, 0, 10))
    p = [x for x in a.positions() if x["id"] == pos["id"]][0]
    assert p["status"] == "closed" and p["exit_reason"] == "손절" and p["sold_qty"] == 500
    assert any("잔고로 다 판 것을 확인" in t for t in _logs(a))


def test_finish_giveup_ambiguous_is_check(env):
    """23시 판정에서 기준값이 없고 잔고에 남아 있으면 '확인 필요'."""
    a, kw, runner, sent = env
    pid = _pos(a, "000009", "다", "2026-10-22", qty=100, now=T(29, 9, 0))
    a._upd("at_positions", pid, T(29, 10, 0), status="closing", exit_order="999", exit_reason="손절")
    kw.manual = {"000009": 100}
    kw.executions = lambda mode: (_ for _ in ()).throw(kiwoom.KiwoomError("키움 응답 오류 502"))
    runner.days = ["2026-10-27", "2026-10-28"]
    a.tick(T(29, 23, 0, 10))
    p = [x for x in a.positions() if x["id"] == pid][0]
    assert p["status"] == "check"


def test_slow_cancel_keeps_tracking_target_fills(env):
    """[돌돌이 D8] 취소 처리가 늦어 매도가 실패해도 옛 목표가 주문을 따라가 그 뒤 체결까지 반영하고,
    취소가 확인되면 남은 수량만 바로 판다."""
    a, kw, runner, sent = env
    kw.strict, kw.cancel_delay = True, 10
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    tno, tp = pos["target_order"], pos["target_price"]
    kw.fill(tno, tp, qty=100)
    a.tick(T(29, 9, 30))
    kw.prices["200710"] = 9_000
    a.tick(T(29, 10, 0))
    p = a.positions("active")[0]
    assert p["status"] == "open" and p["sold_qty"] == 0 and p["old_target"] == tno and p["target_filled"] == 100
    kw.fill(tno, tp, qty=300)  # 취소 처리 전 200주 더 체결
    kw.pending.clear()
    kw.dead.add(tno)  # 이제 취소 처리됨
    a.tick(T(29, 10, 0, 20))
    p = a.positions("active")[0]
    m = kw.find(side="sell", kind="market")
    assert p["status"] == "closing" and p["sold_qty"] == 300 and len(m) == 1 and kw.orders[m[0]]["qty"] == 200
    kw.fill(m[0], 9_000)
    a.tick(T(29, 10, 1))
    p = [x for x in a.positions() if x["id"] == pos["id"]][0]
    assert p["status"] == "closed" and p["sold_qty"] == 500
    assert p["sold_amount"] == pytest.approx(300 * tp + 200 * 9_000) and kw.held("200710") == 0


def test_execution_with_other_code_ignored(env):
    """[돌돌이 9a] 주문번호가 같아도 종목·방향이 다르면 (다른 날 주문번호 겹침) 체결로 보지 않는다."""
    a, kw, runner, sent = env
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    real = kw.executions
    kw.executions = lambda mode: {**real(mode), pos["target_order"]: {"qty": 500, "price": 11_000, "left": 0, "ordered": 500,
                                                                       "code": "999999", "side": "sell", "kind": "order"}}
    a.tick(T(29, 10, 0))
    a.tick(T(29, 10, 1))
    assert a.positions("active")[0]["status"] == "open"


def test_hold_counter_cleared_when_balance_catches_up(env):
    """[돌돌이 9c] 잔고 반영 지연으로 한 번 미룬 뒤 정상으로 닫히면 카운터가 남지 않는다."""
    a, kw, runner, sent = env
    pos = _bought(a, kw, runner)
    real = kw.balance
    lag = {"on": True}
    kw.balance = lambda mode: ({"assets": 5e7, "holdings": [{"code": "200710", "qty": 286}]} if lag["on"] else real(mode))
    kw.fill(pos["target_order"], pos["target_price"])
    a.tick(T(29, 10, 0))
    assert a._hold.get(pos["id"]) == T(29, 10, 0)
    lag["on"] = False
    a.tick(T(29, 10, 0, 20))
    assert pos["id"] not in a._hold


def test_partial_manual_sell_carried_to_next_morning(env):
    """[돌돌이 9e] 마감까지 덜 판 직접 매도·손절도 '다음 거래일 장 시작에 이어서' — 문구대로 실제로 이어서 판다."""
    a, kw, runner, sent = env
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    a.manual_sell(pos["id"], T(29, 14, 0))
    m = kw.find(side="sell", kind="market")[0]
    kw.fill(m, 10_100, qty=300)
    a.tick(T(29, 15, 32))
    p = a.positions("active")[0]
    assert p["status"] == "open" and p["carry"] == "직접 매도" and p["sold_qty"] == 300
    assert any("다음 거래일 장 시작에 이어서" in t for t in _logs(a))
    runner.days.append("2026-10-29")
    a.tick(T(30, 8, 50))
    assert len(kw.find(side="sell", kind="limit")) == 1  # 이어서 팔 종목엔 목표가를 다시 걸지 않는다
    a.tick(T(30, 9, 0, 30))
    m2 = [n for n in kw.find(side="sell", kind="market") if n != m]
    assert len(m2) == 1 and kw.orders[m2[0]]["qty"] == 200
    kw.fill(m2[0], 10_000)
    a.tick(T(30, 9, 1))
    p = [x for x in a.positions() if x["id"] == pos["id"]][0]
    assert p["status"] == "closed" and p["exit_reason"] == "직접 매도" and p["sold_qty"] == 500


def test_resolve_close_and_errors(env):
    a, kw, runner, sent = env
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    with pytest.raises(ValueError):
        a.resolve(pos["id"], "close", T(29, 10, 0))  # 확인 필요 상태가 아님
    a._need_check(T(29, 10, 0), pos, "시험")
    with pytest.raises(ValueError):
        a.manual_sell(pos["id"], T(29, 10, 1))
    with pytest.raises(ValueError):
        a.resolve(pos["id"], "x", T(29, 10, 1))
    kw.prices["200710"] = 10_500
    a.resolve(pos["id"], "close", T(29, 10, 2))
    p = [x for x in a.positions() if x["id"] == pos["id"]][0]
    assert p["status"] == "closed" and p["exit_reason"] == "키움에서 직접 매도" and p["exit_price"] == 10_500


# ---------------------------------------------------------------- 3차: 돌돌이 재현을 '고쳐졌으면 통과'로
def test_cancel_not_confirmed_with_user_shares_waits(env):
    """[돌돌이 N1] 같은 종목 개인 보유분이 있으면 목표가 취소가 '확인'될 때까지 시장가를 내지 않는다
    (취소 접수 후 처리 전 시장가가 접수되면 살아 있는 목표가와 겹쳐 개인 주식이 팔린다). 2분 넘게 확인 안 되면 알림."""
    a, kw, runner, sent = env
    kw.strict, kw.cancel_delay = True, 3  # 미체결 조회 안 됨 (list_open False)
    kw.manual = {"200710": 500}
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    tno = pos["target_order"]
    kw.prices["200710"] = 9_000
    a.tick(T(29, 10, 0))
    assert not kw.find(side="sell", kind="market")
    p = a.positions("active")[0]
    assert p["status"] == "open" and p["old_target"] == tno
    assert not any("목표가 주문을 확인" in b for _, b in sent)
    a.tick(T(29, 10, 1))
    a.tick(T(29, 10, 2, 10))  # 2분 지남 → 매도 멈춤 알림 (팔지 않음)
    assert not kw.find(side="sell", kind="market")
    assert sum("매도 멈춤" in t for t, _ in sent) == 1
    kw.pending.clear()
    kw.dead.add(tno)  # 키움이 취소 처리
    kw.list_open = True  # 미체결 조회로 '없음' 확인
    a.tick(T(29, 10, 3))
    m = kw.find(side="sell", kind="market")
    assert len(m) == 1 and kw.orders[m[0]]["qty"] == 500
    kw.fill(m[0], 9_000)
    a.tick(T(29, 10, 4))
    assert [x for x in a.positions() if x["id"] == pos["id"]][0]["status"] == "closed" and kw.held("200710") == 500


def test_cancel_confirmed_by_open_orders_then_sells(env):
    """[3차-7d] 미체결 조회가 되면 처리 대기 중인 취소(목록에 남음)는 기다리고, 목록에서 빠지면 바로 판다 (개인 보유분 있음).
    일부 체결된 살아 있는 목표가도 목록에 보인다."""
    a, kw, runner, sent = env
    kw.strict, kw.cancel_delay, kw.list_open = True, 99, True
    kw.manual = {"200710": 300}
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    tno, tp = pos["target_order"], pos["target_price"]
    kw.fill(tno, tp, qty=100)
    assert tno in kw.open_orders("mock") and kw.open_orders("mock")[tno]["left"] == 400
    a.tick(T(29, 9, 30))
    kw.prices["200710"] = 9_000
    a.tick(T(29, 10, 0))
    assert not kw.find(side="sell", kind="market")  # 취소가 아직 처리 안 됨 (미체결 목록에 있음)
    kw.pending.clear()
    kw.dead.add(tno)
    a.tick(T(29, 10, 0, 20))
    m = kw.find(side="sell", kind="market")
    p = a.positions("active")[0]
    assert len(m) == 1 and kw.orders[m[0]]["qty"] == 400 and p["sold_qty"] == 100 and not p["old_target"]


def test_balance_lag_40s_closes_normally(env):
    """[돌돌이 N3] 잔고 반영이 40초 늦어도 '확인 필요'로 멈추지 않고 정상으로 닫는다 (90초 기준)."""
    a, kw, runner, sent = env
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    real = kw.balance
    lag = {"on": True}
    kw.balance = lambda mode: ({"assets": 5e7, "holdings": [{"code": "200710", "qty": 500}]} if lag["on"] else real(mode))
    kw.fill(pos["target_order"], pos["target_price"])
    a.tick(T(29, 10, 0))
    a.tick(T(29, 10, 0, 20))
    lag["on"] = False
    a.tick(T(29, 10, 0, 40))
    p = [x for x in a.positions() if x["id"] == pos["id"]][0]
    assert p["status"] == "closed" and p["exit_reason"] == "목표" and not any("확인 필요" in t for t, _ in sent)


def test_check_from_long_lag_releases_itself(env):
    """[돌돌이 N3·3차-1] 잔고 반영이 90초 넘게 늦어 '확인 필요'가 됐어도, 잔고가 따라오면 스스로 닫힌다."""
    a, kw, runner, sent = env
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    real = kw.balance
    lag = {"on": True}
    kw.balance = lambda mode: ({"assets": 5e7, "holdings": [{"code": "200710", "qty": 500}]} if lag["on"] else real(mode))
    kw.fill(pos["target_order"], pos["target_price"])
    a.tick(T(29, 10, 0))
    a.tick(T(29, 10, 1, 40))
    p = a.positions("active")[0]
    assert p["status"] == "check" and p["claim_qty"] == 500 and p["base_qty"] == 500
    lag["on"] = False
    a.tick(T(29, 10, 3))
    p = [x for x in a.positions() if x["id"] == pos["id"]][0]
    assert p["status"] == "closed" and p["exit_reason"] == "목표" and p["sold_qty"] == 500
    assert p["sold_amount"] == pytest.approx(500 * pos["target_price"])


def test_stop_waits_for_balance_lag_instead_of_partial(env):
    """[돌돌이 N4] 손절 때 잔고가 우리 몫보다 조금 적게 보이면(반영 지연) 일부만 팔지 않고 미룬다."""
    a, kw, runner, sent = env
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    real = kw.balance
    lag = {"on": True}
    kw.balance = lambda mode: ({"assets": 5e7, "holdings": [{"code": "200710", "qty": 450}]} if lag["on"] else real(mode))
    kw.prices["200710"] = 9_000
    a.tick(T(29, 10, 0))
    assert not kw.find(side="sell", kind="market")
    lag["on"] = False
    a.tick(T(29, 10, 0, 20))
    m = kw.find(side="sell", kind="market")
    assert len(m) == 1 and kw.orders[m[0]]["qty"] == 500
    kw.fill(m[0], 9_000)
    a.tick(T(29, 10, 0, 40))
    p = [x for x in a.positions() if x["id"] == pos["id"]][0]
    assert p["status"] == "closed" and p["exit_reason"] == "손절"


def _short_balance(kw, n=50):
    real = kw.balance

    def short(mode):  # 키움 잔고가 계속 n주 적게 보임 (키움 앱에서 직접 판 경우 등)
        b = real(mode)
        q = next((h["qty"] for h in b["holdings"] if h["code"] == "200710"), 0)
        return {**b, "holdings": [{"code": "200710", "qty": max(0, q - n)}]}

    kw.balance = short


def test_stop_short_balance_fixed_by_reconcile(env):
    """[3차-5] 잔고가 계속 적으면 2분 간격 잔고 확인이 '키움에서 직접 일부 매도'로 기록을 맞추고, 손절은 남은 만큼 판다."""
    a, kw, runner, sent = env
    _bought(a, kw, runner, close=10_000, fill=10_000)
    _short_balance(kw)
    kw.prices["200710"] = 9_000
    a.tick(T(29, 10, 0))
    a.tick(T(29, 10, 1))
    assert not kw.find(side="sell", kind="market")
    a.tick(T(29, 10, 2))
    m = kw.find(side="sell", kind="market")
    assert len(m) == 1 and kw.orders[m[0]]["qty"] == 450 and any("직접 일부 매도" in t for t in _logs(a))


def test_stop_sells_balance_limit_after_3min(env, monkeypatch):
    """[3차-5] 잔고가 3분 넘게 적게 보이면 손절을 더 미루지 않고 잔고 한도로 팔고 기록한다."""
    a, kw, runner, sent = env
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    monkeypatch.setattr(a, "_reconcile", lambda *x, **k: None)  # 잔고 확인이 기록을 못 맞추는 경우만 따로 본다
    _short_balance(kw)
    kw.prices["200710"] = 9_000
    for ss in (0, 60, 120, 170):
        a.tick(T(29, 10, 0) + dt.timedelta(seconds=ss))
    assert not kw.find(side="sell", kind="market")
    a.tick(T(29, 10, 3, 10))
    m = kw.find(side="sell", kind="market")
    assert len(m) == 1 and kw.orders[m[0]]["qty"] == 450
    kw.fill(m[0], 9_000)
    a.tick(T(29, 10, 3, 30))
    p = [x for x in a.positions() if x["id"] == pos["id"]][0]
    assert p["status"] == "closed" and p["sold_qty"] == 450 and p["ret"] == pytest.approx(-10.0)
    assert any("모자란 50주" in t for t in _logs(a))


def test_check_position_alerts_below_stop_and_reminds(env):
    """[돌돌이 N5·3차-2] '확인 필요' 종목은 자동으로 팔지 않지만, 손절선 아래면 바로 알리고(하루 한 번)
    08:50·12:00·14:00 에 '확인 필요 N종목' 을 다시 알린다."""
    a, kw, runner, sent = env
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    a._need_check(T(29, 9, 10), a.positions("active")[0], "시험")
    kw.prices["200710"] = 7_000
    for hh, mm in ((10, 0), (11, 0), (12, 0), (12, 5), (13, 0), (14, 0), (15, 0)):
        a.tick(T(29, hh, mm))
    assert not kw.find(side="sell", kind="market")
    assert sum("손절선 아래" in t for t, _ in sent) == 1
    assert sum("확인 필요 1종목" in t for t, _ in sent) == 2  # 12:00 · 14:00
    runner.days.append("2026-10-29")
    a.tick(T(30, 8, 50))
    assert sum("확인 필요 1종목" in t for t, _ in sent) == 3


def test_resolve_resume_and_refuse_when_order_alive(env):
    """[3차-7c·7e] 다시 관리: 키움에 그 종목 주문이 아직 살아 있으면 거절, 없으면 open 으로 돌아가 목표가를 다시 건다."""
    a, kw, runner, sent = env
    kw.list_open = True
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    tno = pos["target_order"]
    a._need_check(T(29, 9, 10), a.positions("active")[0], "시험", old_target=tno, target_order=None)
    with pytest.raises(ValueError, match="아직 걸려"):
        a.resolve(pos["id"], "resume", T(29, 9, 11))
    kw.dead.add(tno)  # 사용자가 키움 앱에서 취소
    a.resolve(pos["id"], "resume", T(29, 9, 12))
    p = a.positions("active")[0]
    assert p["status"] == "open" and not p["old_target"] and not p["check_msg"] and p["base_qty"] is None
    a.tick(T(29, 9, 13))
    new = [n for n in kw.find(side="sell", kind="limit") if n != tno]
    assert len(new) == 1 and kw.orders[new[0]]["qty"] == 500


def test_resolve_check_plan(env):
    """[3차-7a] '확인 필요' 매수 후보: 키움 앱에서 확인한 대로 '샀음(수량·가격)' 또는 '안 샀음'."""
    a, kw, runner, sent = env
    runner.rows["2026-10-23"] = [{"code": "000001", "name": "가", "signal_date": "2026-10-23", "close": 10_000, "value": 5e9},
                                 {"code": "000002", "name": "나", "signal_date": "2026-10-23", "close": 10_000, "value": 6e9}]
    a.tick(T(23, 17, 0))
    for p in a.plans("2026-10-26"):
        a.approve(p["id"], True, T(23, 18, 0))
    p1, p2 = a.plans("2026-10-26")
    for p in (p1, p2):
        a._upd("at_plans", p["id"], T(26, 15, 32), status="check")
    with pytest.raises(ValueError):
        a.resolve_plan(p1["id"], "bought", T(26, 16, 0), qty=9_999, price=10_000)
    a.resolve_plan(p1["id"], "bought", T(26, 16, 0), qty=300, price=10_050)
    a.resolve_plan(p2["id"], "not_bought", T(26, 16, 0))
    pos = a.positions("active")
    assert len(pos) == 1 and pos[0]["qty"] == 300 and pos[0]["entry_price"] == 10_050 and pos[0]["entry_date"] == "2026-10-26"
    st = {p["code"]: p["status"] for p in a.plans("2026-10-26")}
    assert st == {"000001": "filled", "000002": "failed"}
    with pytest.raises(ValueError):
        a.resolve_plan(p2["id"], "not_bought", T(26, 16, 1))


def test_manual_sell_deferred_shows_reason(env):
    """개인 보유분이 있고 목표가 취소가 확인 안 되면 '지금 팔기'는 이유를 알려 주고 팔지 않는다 (잠시 뒤 다시)."""
    a, kw, runner, sent = env
    kw.strict, kw.cancel_delay = True, 5
    kw.manual = {"200710": 500}
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    with pytest.raises(ValueError, match="취소가 아직 확인되지"):
        a.manual_sell(pos["id"], T(29, 10, 0))
    assert not kw.find(side="sell", kind="market")


# ---------------------------------------------------------------- 4차: 돌돌이 재현을 '고쳐졌으면 통과'로
def test_balance_down_uses_recent_balance_for_stop(env):
    """[돌돌이 X1] 보통 계좌(개인분 없음)에서 잔고 조회만 실패해도 손절이 막히던 문제: 5분 안에 정상으로 읽은 잔고에서
    그 종목이 우리 몫뿐이었으면 바로 판다. 잔고가 돌아오면 정상으로 닫힌다."""
    a, kw, runner, sent = env
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    a.tick(T(29, 9, 58))  # 정상 잔고 읽음
    real = kw.balance
    kw.balance = lambda mode: (_ for _ in ()).throw(kiwoom.KiwoomError("키움 응답 오류 502"))
    kw.prices["200710"] = 9_000
    a.tick(T(29, 10, 0))
    m = kw.find(side="sell", kind="market")
    assert len(m) == 1 and kw.orders[m[0]]["qty"] == 500
    p = a.positions("active")[0]
    assert p["status"] == "closing" and p["base_qty"] is None  # 예전 잔고는 기준값으로 쓰지 않는다
    kw.fill(m[0], 9_000)
    a.tick(T(29, 10, 0, 20))
    assert a.positions("active")  # 잔고를 못 읽는 동안은 닫지 않고 기다린다
    kw.balance = real
    a.tick(T(29, 10, 0, 40))
    p = [x for x in a.positions() if x["id"] == pos["id"]][0]
    assert p["status"] == "closed" and p["exit_reason"] == "손절"


def test_balance_down_without_recent_balance_defers_with_cause(env):
    """최근 5분 안의 정상 잔고도 없으면 보류하고, 미룬 이유는 '잔고를 못 읽어'로 적는다."""
    a, kw, runner, sent = env
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    kw.balance = lambda mode: (_ for _ in ()).throw(kiwoom.KiwoomError("키움 응답 오류 502"))
    kw.prices["200710"] = 9_000
    a.tick(T(29, 10, 0))
    assert not kw.find(side="sell", kind="market")
    assert any("잔고를 못 읽어" in t for t in _logs(a))
    with pytest.raises(ValueError, match="잔고를 못 읽어"):
        a.manual_sell(pos["id"], T(29, 10, 1))


def test_defer_messages_name_the_cause(env):
    """[4차-2] 보류 문구가 원인별: 개인분이면 '앱 기록에 없는 같은 종목 주식(개인분 N주)'."""
    a, kw, runner, sent = env
    kw.strict, kw.cancel_delay = True, 99
    kw.manual = {"200710": 120}
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    with pytest.raises(ValueError, match="개인분 120주"):
        a.manual_sell(pos["id"], T(29, 10, 0))


def test_stale_short_wait_cleared(env):
    """[돌돌이 X2] 미룬 기록이 남아 나중의 잠깐 반영 지연에서 3분 유예 없이 일부만 팔던 문제: 다시 안 미룬 기록은 지운다."""
    a, kw, runner, sent = env
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    real = kw.balance
    kw.balance = lambda mode: {"assets": 5e7, "holdings": []}  # 손절 순간 잔고가 0으로 보임
    kw.prices["200710"] = 9_000
    a.tick(T(29, 10, 0))
    assert not kw.find(side="sell", kind="market") and a._wait
    kw.balance = real
    kw.prices["200710"] = 9_500  # 손절선 위로 회복 → 그 뒤 매도 시도 없음
    a.tick(T(29, 10, 1))
    a.tick(T(29, 10, 2))
    assert not a._wait  # 다시 안 미룬 기록은 지워졌다
    kw.balance = lambda mode: {"assets": 5e7, "holdings": [{"code": "200710", "qty": 450}]}
    kw.prices["200710"] = 9_000
    a.tick(T(29, 13, 0))
    assert not kw.find(side="sell", kind="market")  # 다시 3분 유예
    kw.balance = real
    a.tick(T(29, 13, 0, 20))
    m = kw.find(side="sell", kind="market")
    assert len(m) == 1 and kw.orders[m[0]]["qty"] == 500
    assert pos["id"]


def test_wait_keys_have_date(env):
    """[4차-3] 미룬 기록 키에 날짜가 들어간다 (다른 날로 넘어가지 않게)."""
    a, kw, runner, sent = env
    _bought(a, kw, runner, close=10_000, fill=10_000)
    kw.balance = lambda mode: {"assets": 5e7, "holdings": []}
    kw.prices["200710"] = 9_000
    a.tick(T(29, 10, 0))
    assert a._wait and all(k[-1] == "2026-10-29" for k in a._wait)


def test_deferred_position_cheap_on_later_ticks(env):
    """[돌돌이 X3] 개인분 + 취소 확인 불가로 보류된 종목: 처음에만 1.5초×4 다시 보고, 다음 확인부터는
    취소를 다시 보내지 않고 기다림 없이 계좌 조회 몇 건(체결·미체결·잔고 각 1번)만."""
    a, kw, runner, sent = env
    kw.strict, kw.cancel_delay = True, 99
    kw.manual = {"200710": 500}
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    kw.prices["200710"] = 9_000
    import sbt.autotrade as at
    n = {"ex": 0, "oo": 0, "bal": 0, "cancel": 0, "sleep": 0}
    rex, roo, rbal, rc = kw.executions, kw.open_orders, kw.balance, kw.cancel

    def count(key, fn):
        def f(*x):
            n[key] += 1
            return fn(*x)
        return f

    kw.executions, kw.open_orders, kw.balance, kw.cancel = (count("ex", rex), count("oo", roo), count("bal", rbal),
                                                          count("cancel", rc))
    real_sleep = at.time.sleep
    at.time.sleep = lambda s: n.__setitem__("sleep", n["sleep"] + 1)
    try:
        a.tick(T(29, 10, 0))
        first = dict(n)
        for k in n:
            n[k] = 0
        a.tick(T(29, 10, 0, 20))
    finally:
        at.time.sleep = real_sleep
    assert first["cancel"] == 1 and first["sleep"] >= 4  # 처음 보류: 취소 1번 + 잠깐 다시 보기
    assert n["sleep"] == 0 and n["cancel"] == 0
    assert n["ex"] + n["oo"] + n["bal"] <= 3, n
    assert not kw.find(side="sell", kind="market") and a.positions("active")[0]["old_target"] == pos["target_order"]


def test_check_stop_alert_repeats_every_3pct(env):
    """[4차-4] 확인 필요 종목 손절선 아래 알림: 처음 + 손절선 대비 3%p 더 떨어질 때마다, 하루 최대 4번."""
    a, kw, runner, sent = env
    _bought(a, kw, runner, close=10_000, fill=10_000)  # 손절선 9,300
    a._need_check(T(29, 9, 10), a.positions("active")[0], "시험")
    alerts = lambda: sum("손절선 아래" in t for t, _ in sent)  # noqa: E731
    for mm, px, want in ((10, 9_000, 1), (11, 9_000, 1), (12, 8_700, 2), (13, 8_700, 2), (14, 8_400, 3),
                         (15, 8_100, 4), (16, 7_800, 4), (17, 7_000, 4)):
        kw.prices["200710"] = px
        a.tick(T(29, 10, mm))
        assert alerts() == want, (px, alerts())


def test_finished_unknown_when_executions_fail(env):
    """[4차-5] 체결 조회가 (쪽수 상한 초과 등으로) 실패하면 '끝남'으로 보지 않는다 ('모름')."""
    a, kw, runner, sent = env
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    kw.executions = lambda mode: (_ for _ in ()).throw(kiwoom.KiwoomError("ka10076 페이지가 너무 많습니다"))
    assert a._finished("mock", pos["target_order"], "200710") == (False, None)


def test_old_miss_does_not_count_as_second(env):
    """[돌돌이 X2] 잔고 확인의 '모자람' 기록이 오래 남아 몇 시간 뒤 한 번의 반영 지연을 '키움에서 직접 매도'로 보던 문제."""
    a, kw, runner, sent = env
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    real = kw.balance
    kw.balance = lambda mode: {"assets": 5e7, "holdings": []}
    a.tick(T(29, 10, 0))  # 잔고가 잠깐 0 으로 보임 (한 번)
    kw.balance = real
    a.tick(T(29, 10, 1))  # (2분 간격이라 잔고 확인은 안 돎)
    kw.balance = lambda mode: {"assets": 5e7, "holdings": [{"code": "200710", "qty": 450}]}
    a.tick(T(29, 13, 0))  # 세 시간 뒤 한 번 더 잠깐 모자람 → 아직 판단하지 않는다
    kw.balance = real
    p = a.positions("active")[0]
    assert p["sold_qty"] == 0 and not any("직접" in t for t in _logs(a))
    a.tick(T(29, 13, 2, 30))
    assert a.positions("active")[0]["sold_qty"] == 0 and pos["id"]


def test_cancel_not_sent_is_retried_and_stop_goes_out(env):
    """목표가 취소가 '연결 실패'로 안 나갔으면 old_target 으로 남아도 1분 뒤 취소를 다시 보내고 손절이 나간다
    (다시 안 보내면 목표가가 살아 있는 채로 손절·기간만료가 하루 종일 막힘). 돌돌이 4차 Y1."""
    a, kw, runner, sent = env
    kw.strict = True
    pos = _bought(a, kw, runner, close=10_000, fill=10_000)
    tno = pos["target_order"]
    real_c = kw.cancel
    fail = {"n": 1}

    def c(mode, code, no):
        if fail["n"]:
            fail["n"] -= 1
            raise kiwoom.KiwoomError("키움 서버에 연결하지 못했습니다: Max retries exceeded")
        return real_c(mode, code, no)

    kw.cancel = c
    kw.prices["200710"] = 9_000
    t = T(29, 10, 0)
    while t <= T(29, 10, 10):
        a.tick(t)
        t += dt.timedelta(seconds=20)
    assert tno in kw.cancelled and kw.find(side="sell", kind="market")
    # 접수된 취소는 다시 보내지 않는다
    n = len(kw.cancelled)
    a.tick(T(29, 10, 12))
    assert len(kw.cancelled) == n
