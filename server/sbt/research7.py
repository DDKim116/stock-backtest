"""신고가 추세 v1 다듬기 + 실제 자금으로 합쳐 굴려 보기.

  python -m sbt.data.cli trend-test

1부. 신고가 추세 v1 의 시장 조건을 바꿔 비교 (거래 1건당 평균, 상승기·하락기·강세장·약세장 해로 나눠서)
2부. 지금 자동 매매 구성(v2b 500만×하루 10 + 중간값 300만×하루 5, 앱에 저장된 중간값 전략이 있으면)에
     신고가 추세를 더했을 때 5천만원 계좌가 어떻게 되는지. 돈은 같이 쓰고, 같은 날은 v2b → 중간값 → 신고가 순,
     같은 전략 안에서는 거래대금 작은 순 (앱 자동 매매와 같은 규칙). 이미 가진 종목은 다시 사지 않는다.
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import breadth, config
from .data.indices import attach as indices_attach
from .data.panel import Panel
from .engine import dsl, event
from .engine.run import Runner
from .engine.spec import parse_text
from .engine.stats import summarize
from .research import _family_spec
from .research2 import Ctx, _fmt, _signals, _sim
from .research6 import _years

CUT = "2021-01-01"
CAPITAL = 50_000_000

TREND = """[신호]
종가 > 이전(최고(고가, 250), 1)
그리고 거래량 >= 이평(거래량, 20) * 1.5
그리고 {market}
[매수]
다음날시가
[청산]
목표 = 없음
손절 = 10
보유기간 = 120
청산조건 = {exit}"""
STOCK_EXIT = "종가 < 이평(종가, 50)"
MKT_EXIT = "종가 < 이평(종가, 50) 또는 지수 < 이평(지수, 60)"

VARIANTS: list[tuple[str, str, str]] = [
    ("A 지금: 지수 60일선 위", "지수 > 이평(지수, 60)", STOCK_EXIT),
    ("B 지수 120일선 위", "지수 > 이평(지수, 120)", STOCK_EXIT),
    ("C 지수 정배열(60일선 위, 60>120)", "지수 > 이평(지수, 60) 그리고 이평(지수, 60) > 이평(지수, 120)", STOCK_EXIT),
    ("D 60일선 위 + 상승비율 20일 평균 50↑", "지수 > 이평(지수, 60) 그리고 이평(상승비율, 20) >= 50", STOCK_EXIT),
    ("E 60일선 위 + 동일가중도 60일선 위", "지수 > 이평(지수, 60) 그리고 동일가중 > 이평(동일가중, 60)", STOCK_EXIT),
    ("F 정배열 + 동일가중 60일선 위", "지수 > 이평(지수, 60) 그리고 이평(지수, 60) > 이평(지수, 120) "
                                 "그리고 동일가중 > 이평(동일가중, 60)", STOCK_EXIT),
    ("G A + 지수 60일선 깨면 팔기", "지수 > 이평(지수, 60)", MKT_EXIT),
    ("H F + 지수 60일선 깨면 팔기", "지수 > 이평(지수, 60) 그리고 이평(지수, 60) > 이평(지수, 120) "
                               "그리고 동일가중 > 이평(동일가중, 60)", MKT_EXIT),
]

V2B = """[신호]
수익률(5) <= -15
그리고 rsi(14) <= 25
그리고 지수 <= 이전(지수, 5) * 0.97
[매수]
다음날시가
[청산]
목표 = 10
손절 = 7
보유기간 = 5"""


class Leg:
    """한 전략의 거래들 (체결된 것만)."""

    def __init__(self, name: str, runner: Runner, spec, per: float, daily: int):
        p = runner.panel
        t = _sim(runner, spec, _signals(runner, spec))
        ok = t.fill_row >= 0
        self.name, self.per, self.daily = name, per, daily
        self.code = p.codes[t.sig[ok]].astype(str)
        self.fill = p.dates[t.fill_row[ok]]
        self.done = t.outcome_c[ok] != event.OPEN
        er = t.exit_row_c[ok]
        self.exit = np.where(er >= 0, p.dates[np.maximum(er, 0)], np.datetime64("NaT"))
        self.ret = np.where(self.done, t.ret_c[ok], 0.0)
        v = p.cols.get("value")
        self.value = v[t.sig[ok]] if v is not None else np.zeros(ok.sum())


def portfolio(legs: list[Leg], capital: float = CAPITAL) -> dict:
    """돈을 같이 쓰는 계좌 (실현 손익 기준). 매도 대금은 다음 거래일부터 다시 쓴다."""
    rows = []
    for i, g in enumerate(legs):
        for k in range(len(g.fill)):
            rows.append((g.fill[k], i, np.nan_to_num(g.value[k], nan=np.inf), k))
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    cash = capital
    open_: list[tuple] = []  # (청산일, 돌아올 돈, 원금, 코드, 전략)
    held: set[str] = set()
    per_day: dict = {}
    realized: list[tuple] = []
    taken = [0] * len(legs)
    skipped = 0
    stuck = 0.0
    for d, i, _, k in rows:
        keep = []
        for e, back, cost, code, li in open_:
            if e < d:
                cash += back
                held.discard(code)
                realized.append((e, back - cost, li))
            else:
                keep.append((e, back, cost, code, li))
        open_ = keep
        g = legs[i]
        if g.code[k] in held or per_day.get((d, i), 0) >= g.daily:
            continue
        if cash < g.per:
            skipped += 1
            continue
        cash -= g.per
        per_day[(d, i)] = per_day.get((d, i), 0) + 1
        taken[i] += 1
        held.add(g.code[k])
        if g.done[k]:
            open_.append((g.exit[k], g.per * (1 + g.ret[k] / 100), g.per, g.code[k], i))
        else:
            stuck += g.per  # 끝나지 않은 거래는 원금으로 묶어 둔다
    for e, back, cost, code, li in open_:
        cash += back
        realized.append((e, back - cost, li))
    final = cash + stuck
    rs = pd.DataFrame(realized, columns=["d", "pnl", "leg"])
    rs["d"] = pd.to_datetime(rs["d"])
    rs = rs.sort_values("d")
    eq = capital + rs["pnl"].cumsum()
    mdd = float(((eq / eq.cummax()) - 1).min() * 100) if len(eq) else 0.0
    years = max((rs["d"].iloc[-1] - rs["d"].iloc[0]).days / 365.25, 0.5) if len(rs) else 1
    by_year = rs.groupby(rs["d"].dt.year)["pnl"].sum() / capital * 100
    by_leg = rs.groupby("leg")["pnl"].sum()
    cut = pd.Timestamp(CUT)
    is_y = by_year[by_year.index < cut.year]
    oos_y = by_year[by_year.index >= cut.year]
    return {"final": final, "cagr": ((final / capital) ** (1 / years) - 1) * 100, "mdd": mdd,
            "by_year": by_year, "taken": taken, "skipped": skipped,
            "pnl_leg": [float(by_leg.get(i, 0.0)) for i in range(len(legs))],
            "is_avg": float(is_y.mean()) if len(is_y) else None, "oos_avg": float(oos_y.mean()) if len(oos_y) else None}


def run(dataset: str = "kr", log=print) -> Path:
    t0 = time.time()
    root = config.dataset_dir(dataset)
    log("데이터 읽는 중…")
    panel = Panel.load(root / "prices.parquet")
    if not indices_attach(panel, dataset):
        raise SystemExit("지수 데이터가 없습니다. 먼저 index-update 를 실행하세요.")
    breadth.attach(panel)
    runner = Runner(panel, {"name": dataset, "markets": ["KOSPI", "KOSDAQ"], "currency": "KRW"})
    ctx = Ctx(runner, np.datetime64(CUT, "D"))
    bears, bulls = _years("bear"), _years("bull")
    up_day = dsl.evaluate("지수 > 이평(지수, 60)", panel)
    L = [f"신고가 추세 다듬기 + 실제 자금 비교 · {dt.date.today()} · 국내 2010~ · 과거 ~{CUT} / 검증 {CUT}~",
         f"약세장 해: {', '.join(map(str, bears))} · 강세장 해: {', '.join(map(str, bulls))}",
         "", "1부. 신고가 추세 v1 시장 조건 비교 (비용 차감, 거래 1건당 평균)"]
    specs = {}
    for name, market, exit_ in VARIANTS:
        log(f"1부 {name}…")
        spec = _family_spec(TREND.format(market=market, exit=exit_), ["코스피", "코스닥"])
        specs[name] = spec
        t = _sim(runner, spec, _signals(runner, spec))
        st = ctx.stats(t, spec)
        yrs = panel.dates[t.sig].astype("datetime64[Y]").astype(int) + 1970
        L.append("")
        L.append(f"■ {name}")
        L.append(ctx.line("전체", st))
        parts = []
        for label, m in (("상승기 날", up_day[t.sig]), ("강세장 해", np.isin(yrs, bulls)), ("약세장 해", np.isin(yrs, bears))):
            x = summarize(t, spec, m) if m.any() else None
            if x and x["n_complete"]:
                parts.append(f"{label} {_fmt(x['avg_ret'], 2)}({x['n_complete']:,})")
        L.append("  " + " · ".join(parts))
        row = []
        for y in np.unique(yrs):
            s_ = summarize(t, spec, yrs == y)
            if s_["n_complete"]:
                mark = "*" if y in bears else ("+" if y in bulls else "")
                row.append(f"{y}{mark} {_fmt(s_['avg_ret'], 1)}({s_['n_complete']})")
        for k in range(0, len(row), 6):
            L.append("    " + "  ".join(row[k:k + 6]))

    # 2부: 실제 자금
    log("2부 v2b…")
    base = [Leg("v2b", runner, _family_spec(V2B, ["코스피", "코스닥"]), 5_000_000, 10)]
    mid = None
    try:
        from .api.store import Store

        store = Store(config.DATA_DIR / "app.sqlite3")
        mid = next((x for x in store.list() if "중간값" in x["name"]), None)
    except Exception:  # noqa: BLE001
        mid = None
    if mid:
        log("2부 중간값…")
        base.append(Leg("중간값", runner, parse_text(mid["text"]), 3_000_000, 5))
    L += ["", "2부. 실제 자금으로 굴리기 · 자본 5천만원 · 돈 같이 쓰기 · 실현 손익 기준 · 비용 차감",
          f"지금 구성: v2b 500만×하루 10" + (f" + 중간값('{mid['name']}') 300만×하루 5" if mid else " (앱에 중간값 전략이 없어 v2b만)"),
          "신고가 추세는 500만×하루 3 으로 더함", ""]

    def line(label: str, r: dict, legs: list[Leg]) -> list[str]:
        out = [f"■ {label}",
               f"  최종 {r['final'] / 1e8:,.2f}억 · 연 {r['cagr']:+.1f}% · 최대 낙폭 {r['mdd']:.1f}% · "
               f"해마다 평균 과거 {_fmt(r['is_avg'], 1)} / 검증 {_fmt(r['oos_avg'], 1)} · 돈 부족으로 못 산 {r['skipped']:,}건",
               "  " + " · ".join(f"{g.name} {n:,}건 {p / 1e4:+,.0f}만" for g, n, p in zip(legs, r["taken"], r["pnl_leg"]))]
        yy = [f"{y}{'*' if y in bears else ('+' if y in bulls else '')} {v:+.1f}%" for y, v in r["by_year"].items()]
        for k in range(0, len(yy), 8):
            out.append("    " + "  ".join(yy[k:k + 8]))
        return out

    log("2부 지금 구성…")
    r0 = portfolio(base)
    L += line("지금 구성", r0, base)
    results = [("지금 구성", r0)]
    for name, *_ in VARIANTS:
        log(f"2부 + {name}…")
        legs = base + [Leg("신고가", runner, specs[name], 5_000_000, 3)]
        r = portfolio(legs)
        results.append((name, r))
        L.append("")
        L += line(f"지금 구성 + 신고가 {name}", r, legs)
    L += ["", "요약 (연 수익률 / 최대 낙폭)"]
    for name, r in results:
        L.append(f"  {name:<32} 연 {r['cagr']:+.1f}% · 낙폭 {r['mdd']:.1f}% · 검증 구간 해마다 {_fmt(r['oos_avg'], 1)}")
    L += ["", "* = 약세장 해, + = 강세장 해", f"소요 {round((time.time() - t0) / 60, 1)}분"]
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"trend-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
