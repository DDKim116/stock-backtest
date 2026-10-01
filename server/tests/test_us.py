"""미국 데이터 수집·환율 환산 테스트 (야후 응답은 가짜로 대체)."""
import io

import numpy as np
import pandas as pd
import pytest

from sbt.data import us
from sbt.data.panel import Panel
from sbt.engine.run import Runner
from sbt.engine.spec import Spec, parse_text, to_text

NASDAQ_TXT = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N
QQQ|Invesco QQQ Trust, Series 1|G|N|N|100|Y|N
ABCDW|ABCD Corp - Warrants|S|N|N|100|N|N
ZZZT|Test Co - Common Stock|S|Y|N|100|N|N
SPCX|Space Acquisition Corp - Class A Ordinary Shares|S|N|N|100|N|N
File Creation Time: 1001202600:00|||||||"""

OTHER_TXT = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
BRK.B|Berkshire Hathaway Inc. Class B Common Stock|N|BRK.B|N|100|N|BRK=B
JPM$C|JPMorgan Chase Preferred|N|JPMpC|N|100|N|JPM-C
SPY|SPDR S&P 500|P|SPY|Y|100|N|SPY
XYZ|XYZ Corp Common Stock|A|XYZ|N|100|N|XYZ
File Creation Time: 1001202600:00||||||||"""


def test_parse_symbols():
    df = us.parse_symbols(pd.read_csv(io.StringIO(NASDAQ_TXT.rsplit("\n", 1)[0]), sep="|", dtype=str),
                          pd.read_csv(io.StringIO(OTHER_TXT.rsplit("\n", 1)[0]), sep="|", dtype=str))
    syms = dict(zip(df["symbol"], df["market"]))
    assert syms == {"AAPL": "NASDAQ", "SPCX": "NASDAQ", "BRK-B": "NYSE", "XYZ": "AMEX"}
    assert df.set_index("symbol").at["AAPL", "name"] == "Apple Inc."


def fake_yf(frames: dict[str, pd.DataFrame]):
    """yf.download(group_by='ticker') 와 같은 모양의 프레임."""
    def download(tickers, **kw):
        cols = {}
        for t in tickers:
            if t in frames:
                f = frames[t].set_index("date")
                for c in ("open", "high", "low", "close", "volume"):
                    cols[(t, c.capitalize())] = f[c]
                cols[(t, "Adj Close")] = f["close"]
        df = pd.DataFrame(cols)
        df.index.name = "Date"
        return df
    return download


def bars(start, closes):
    d = pd.bdate_range(start, periods=len(closes))
    c = np.array(closes, dtype=float)
    return pd.DataFrame({"date": d, "open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1000.0})


def setup_root(tmp_path, monkeypatch, frames):
    import yfinance as yf
    monkeypatch.setattr(yf, "download", fake_yf(frames))
    syms = pd.DataFrame({"symbol": ["AAPL", "XYZ"], "name": ["Apple Inc.", "XYZ Corp"],
                         "market": ["NASDAQ", "AMEX"]})
    monkeypatch.setattr(us, "fetch_symbols", lambda: syms.copy())
    monkeypatch.setattr(us.time, "sleep", lambda s: None)
    return tmp_path / "us"


def test_collect_build_and_split_refetch(tmp_path, monkeypatch):
    fx = bars("2024-01-01", [1300.0] * 30)
    frames = {"AAPL": bars("2024-01-01", list(range(100, 130))), "XYZ": bars("2024-01-01", [5.0] * 30),
              "KRW=X": fx, "SPY": fx}
    root = setup_root(tmp_path, monkeypatch, frames)
    assert us.collect(root, "2024-01-01", log=lambda *a: None) == 2
    us.build(root, log=lambda *a: None)
    df = pd.read_parquet(root / "prices.parquet")
    assert set(df["code"]) == {"AAPL", "XYZ"} and (df["fx"] == 1300).all()

    # 분할: 야후가 과거 가격을 소급 조정 → 겹치는 구간이 달라짐 → 전체 다시 받기
    frames["AAPL"] = bars("2024-01-01", [c / 4 for c in range(100, 135)])
    frames["XYZ"] = bars("2024-01-01", [5.0] * 35)
    us.update(root, log=lambda *a: None)
    a = pd.read_parquet(root / "raw" / "AAPL.parquet")
    assert len(a) == 35 and a["close"].iloc[0] == 25.0
    assert len(pd.read_parquet(root / "raw" / "XYZ.parquet")) == 35


def make_us_panel(fx_values):
    n = len(fx_values)
    d = pd.bdate_range("2024-01-01", periods=n)
    close = [100, 100, 110] + [110] * (n - 3)
    df = pd.DataFrame({"date": d, "code": "AAPL", "name": "Apple Inc.", "market": "NASDAQ",
                       "open": close, "high": close, "low": close, "close": close, "volume": 1000.0,
                       "value": 1e5, "fx": fx_values})
    df.loc[2, "high"] = 111
    return Panel(df)


def test_fx_krw_return():
    p = make_us_panel([1300, 1300, 1300] + [1430] * 5)
    sp = Spec.model_validate({
        "signal": "등락률 >= 10", "entry": {"type": "close"},
        "exit": {"target_pct": None, "max_days": 2}, "universe": {"markets": ["NASDAQ"], "start": "2000-01-01"},
        "costs": {"buy_fee_pct": 0, "sell_fee_pct": 0, "sell_tax_pct": 0},
    })
    r = Runner(p, {"name": "us", "markets": ["NASDAQ", "NYSE", "AMEX"], "currency": "USD"})
    assert r.trades(sp).ret_c[0] == pytest.approx(0.0)
    t = r.trades(sp.model_copy(update={"fx_krw": True}))
    assert t.ret_c[0] == pytest.approx(10.0)  # 달러 0% + 환율 +10%
    res = r.run(sp)
    assert any("달러 기준" in c["text"] for c in res["checks"])
    assert res["breakdowns"]["price"][0]["key"] == "$100~500"


def test_market_mismatch_and_text_option():
    p = make_us_panel([1300] * 8)
    r = Runner(p, {"name": "us", "markets": ["NASDAQ", "NYSE", "AMEX"], "currency": "USD"})
    with pytest.raises(Exception, match="나스닥"):
        r.run(Spec.model_validate({"signal": "등락률 >= 10"}))  # 기본 시장은 코스피·코스닥
    s = parse_text("[신호]\n등락률 >= 10\n[대상]\n시장 = 나스닥, 뉴욕\n[옵션]\n원화환산 = 예\n")
    assert s.fx_krw and s.universe.markets == ["NASDAQ", "NYSE"]
    assert parse_text(to_text(s)) == s


def test_us_spac_flag():
    df = pd.DataFrame({"date": pd.bdate_range("2024-01-01", periods=2).repeat(2), "code": ["A", "B"] * 2,
                       "name": ["Space Acquisition Corp", "Apple Inc."] * 2, "market": "NASDAQ",
                       "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0})
    p = Panel(df.sort_values(["code", "date"]).reset_index(drop=True))
    assert p.is_spac.tolist() == [True, True, False, False] and not p.is_preferred.any()
