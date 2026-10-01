import numpy as np
import pandas as pd
import pytest

from sbt.data.build import adjust
from sbt.data.panel import Panel
from sbt.engine import dsl, event
from sbt.engine.run import Runner
from sbt.engine.spec import Spec, expand, parse_text, to_text


def make_panel(rows_by_code: dict[str, list[tuple]]) -> Panel:
    """rows: (open, high, low, close, volume)"""
    frames = []
    for code, rows in rows_by_code.items():
        d = pd.bdate_range("2020-01-01", periods=len(rows))
        df = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"])
        df.insert(0, "date", d)
        df.insert(1, "code", code)
        df["name"] = f"종목{code}"
        df["market"] = "KOSPI"
        df["value"] = df["close"] * df["volume"]
        df["mktcap"] = df["close"] * 1e6
        frames.append(df)
    return Panel(pd.concat(frames, ignore_index=True))


def spec(**kw) -> Spec:
    base = {"signal": "등락률 >= 10", "universe": {"start": "2000-01-01"},
            "costs": {"buy_fee_pct": 0, "sell_fee_pct": 0, "sell_tax_pct": 0}}
    for k, v in kw.items():
        if isinstance(v, dict):
            base.setdefault(k, {}).update(v)
        else:
            base[k] = v
    return Spec.model_validate(base)


def run_one(panel, sp):
    r = Runner(panel, {"name": "test"})
    t = r.trades(sp)
    return t


# ---------------------------------------------------------------- DSL

def test_dsl_offsets_and_units():
    p = make_panel({"000010": [(100, 100, 100, 100, 10), (100, 120, 100, 115, 60)]})
    v = dsl.evaluate("거래량 >= 거래량(1) * 5 AND 등락률 >= 12", p)
    assert v.tolist() == [False, True]
    v = dsl.evaluate("거래대금 >= 1만", p)  # 100*10=1000, 115*60=6900
    assert v.tolist() == [False, False]
    v = dsl.evaluate("거래대금 >= 5천" if False else "거래대금 >= 0.5만", p)
    assert v.tolist() == [False, True]


def test_dsl_korean_keywords_and_errors():
    p = make_panel({"000010": [(1, 1, 1, 1, 1)] * 3})
    assert dsl.evaluate("종가 > 0 그리고 NOT 종가 > 5", p).all()
    with pytest.raises(dsl.DSLError, match="%"):
        dsl.parse("등락률 >= 12%")
    with pytest.raises(dsl.DSLError):
        dsl.parse("종가 >")
    with pytest.raises(dsl.DSLError, match="알 수 없는 이름"):
        dsl.parse("가격 > 3")


def test_dsl_rolling_does_not_cross_codes():
    p = make_panel({
        "000010": [(10, 10, 10, 10, 1)] * 3,
        "000020": [(20, 20, 20, 20, 1)] * 3,
    })
    ma = dsl.evaluate("이평(종가, 2)", p)
    assert np.isnan(ma[0]) and ma[1] == 10 and ma[2] == 10
    assert np.isnan(ma[3]) and ma[4] == 20  # 앞 종목 값이 섞이지 않음
    prev = dsl.evaluate("종가(1)", p)
    assert np.isnan(prev[3])


def test_dsl_cross_and_count():
    p = make_panel({"000010": [(c, c, c, c, 1) for c in (5, 5, 12, 12, 8)]})
    up = dsl.evaluate("상향돌파(종가, 10)", p)
    assert up.tolist() == [False, False, True, False, False]
    cnt = dsl.evaluate("횟수(종가 > 10, 3)", p)
    assert cnt[4] == 2


# ---------------------------------------------------------------- 체결/청산

def test_limit_fill_intraday_and_target_uncertain():
    # 0: 기준, 1: 신호(+20%), 2: 장중 지정가 체결 + 고가는 목표 이상, 종가는 목표 미만
    rows = [(100, 100, 100, 100, 10),
            (100, 125, 100, 120, 100),  # 신호: 지정가 (100+120)/2 = 110
            (115, 114 + 10, 109, 112, 50),  # 시가 115 > 110, 저가 109 → 110 장중체결. 고가 124 ≥ 113.3
            (112, 112, 111, 111, 50)] + [(111, 111, 111, 111, 50)] * 10
    p = make_panel({"000010": rows})
    t = run_one(p, spec(exit={"target_pct": 3, "max_days": 5}))
    assert len(t) == 1
    assert t.fill_kind[0] == event.FILL_INTRADAY
    assert t.fill_price[0] == 110
    assert t.outcome_c[0] == event.TIMEOUT  # 보수적: 체결 전 고가일 수 있음
    assert t.outcome_o[0] == event.TARGET
    assert t.days_o[0] == 0


def test_limit_fill_at_open_counts_same_day_high():
    rows = [(100, 100, 100, 100, 10),
            (100, 125, 100, 120, 100),  # 지정가 110
            (105, 116, 104, 106, 50),  # 시가 105 ≤ 110 → 시가 체결, 고가 116 ≥ 108.15
            ] + [(106, 106, 106, 106, 50)] * 10
    p = make_panel({"000010": rows})
    t = run_one(p, spec(exit={"target_pct": 3, "max_days": 5}))
    assert t.fill_kind[0] == event.FILL_AT_OPEN and t.fill_price[0] == 105
    assert t.outcome_c[0] == event.TARGET and t.days_c[0] == 0
    assert t.exit_price_c[0] == pytest.approx(105 * 1.03)


def test_no_fill_within_valid_days():
    rows = [(100, 100, 100, 100, 10), (100, 125, 100, 120, 100)] + [(130, 131, 129, 130, 10)] * 10
    p = make_panel({"000010": rows})
    t = run_one(p, spec(entry={"valid_days": 3}))
    assert t.outcome_c[0] == event.NO_FILL


def test_stop_and_target_same_day():
    rows = [(100, 100, 100, 100, 10), (100, 112, 100, 110, 100)] + \
           [(110, 110, 110, 110, 10), (110, 120, 90, 110, 10)] + [(110, 110, 110, 110, 10)] * 10
    p = make_panel({"000010": rows})
    t = run_one(p, spec(entry={"type": "close"}, exit={"target_pct": 5, "stop_pct": 5, "max_days": 5}))
    assert t.outcome_c[0] == event.STOP and t.outcome_o[0] == event.TARGET
    assert t.days_c[0] == 2


def test_gap_up_exit_at_open_and_timeout_and_open():
    rows = [(100, 100, 100, 100, 10), (100, 112, 100, 110, 100), (120, 125, 119, 121, 10)] + \
           [(121, 121, 121, 121, 10)] * 3
    p = make_panel({"000010": rows})
    t = run_one(p, spec(entry={"type": "close"}, exit={"target_pct": 5, "max_days": 3}))
    assert t.outcome_c[0] == event.TARGET and t.exit_price_c[0] == 120  # 시가 갭상승 매도

    rows2 = [(100, 100, 100, 100, 10), (100, 112, 100, 110, 100)] + [(110, 111, 109, 110, 10)] * 3
    t = run_one(make_panel({"000010": rows2}), spec(entry={"type": "close"}, exit={"target_pct": 5, "max_days": 3}))
    assert t.outcome_c[0] == event.TIMEOUT and t.days_c[0] == 3
    t = run_one(make_panel({"000010": rows2}), spec(entry={"type": "close"}, exit={"target_pct": 5, "max_days": 5}))
    assert t.outcome_c[0] == event.OPEN


def test_costs_applied():
    rows = [(100, 100, 100, 100, 10), (100, 112, 100, 110, 100)] + [(110, 111, 109, 110, 10)] * 3
    sp = spec(entry={"type": "close"}, exit={"target_pct": None, "max_days": 2},
              costs={"buy_fee_pct": 0.1, "sell_fee_pct": 0.1, "sell_tax_pct": 0.2})
    t = run_one(make_panel({"000010": rows}), sp)
    assert t.ret_c[0] == pytest.approx(((110 * (1 - 0.003)) / (110 * 1.001) - 1) * 100)


def test_dedupe():
    rows = [(100, 100, 100, 100, 10)] + [(100, 100, 100, 100 * 1.1 ** (i + 1), 10) for i in range(5)]
    p = make_panel({"000010": rows})
    r = Runner(p, {"name": "t"})
    assert len(r.trades(spec(dedupe_days=0))) == 5
    assert len(r.trades(spec(dedupe_days=2))) == 2


# ---------------------------------------------------------------- 수정주가

def test_adjust_split():
    df = pd.DataFrame({
        "date": pd.bdate_range("2020-01-01", periods=4),
        "code": "000010",
        "open": [5000, 5100, 1000, 1010], "high": [5000, 5100, 1000, 1010],
        "low": [5000, 5100, 1000, 1010], "close": [5000, 5000, 1010, 1020],
        "diff": [0, 0, 10, 10],  # 분할일: 기준가 1000 대비 +10
        "volume": [100, 100, 500, 500],
    })
    out = adjust(df)
    assert out["close"].tolist() == pytest.approx([1000, 1000, 1010, 1020])
    assert out["volume"].tolist() == pytest.approx([500, 500, 500, 500])
    assert out["is_adj_event"].tolist() == [False, False, True, False]


# ---------------------------------------------------------------- 명세

def test_text_roundtrip_and_sweep():
    txt = """[신호]
거래량 >= 거래량(1) * 5
AND 등락률 >= [10, 12]
[매수]
지정가 = (시가 + 종가) / 2
유효기간 = 5
[청산]
목표 = [3, 5]
보유기간 = 10
[대상]
시장 = 코스피
기간 = 2015-01-01 ~ 오늘
제외 = 스팩
"""
    s = parse_text(txt)
    assert s.universe.markets == ["KOSPI"] and s.universe.exclude_preferred is False
    v = expand(s)
    assert len(v) == 4
    assert v[1][1].signal.endswith(">= 10") and v[1][1].exit.target_pct == 5
    s2 = parse_text(to_text(s))
    assert s2 == s
