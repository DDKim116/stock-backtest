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
                     "ordered": self.orders[no]["qty"], "code": self.orders[no]["code"],
                     "side": self.orders[no]["side"]} for no, f in self.fills.items()}

    def balance(self, mode):
        q = dict(self.manual)
        for no, f in self.fills.items():
            o = self.orders[no]
            q[o["code"]] = q.get(o["code"], 0) + (f["qty"] if o["side"] == "buy" else -f["qty"])
        return {"holdings": [{"code": c, "qty": n} for c, n in q.items() if n > 0]}

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
