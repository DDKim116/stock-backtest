"""키움 모의투자 자동 매매 성적표 (터미널에서 복사해 붙여 넣기 좋게).

  python -m sbt.data.cli auto-report
"""
from __future__ import annotations

import datetime as dt

from . import autotrade, breadth, config
from .api.store import Store
from .broker import kiwoom
from .data.indices import attach as indices_attach
from .data.panel import Panel
from .engine.run import Runner
from .engine.spec import parse_text


def run(dataset: str = "kr", log=print) -> str:
    panel = Panel.load(config.dataset_dir(dataset) / "prices.parquet")
    indices_attach(panel, dataset)
    breadth.attach(panel)
    runner = Runner(panel, {"name": dataset, "markets": ["KOSPI", "KOSDAQ"], "currency": "KRW"})
    a = autotrade.Auto(Store(config.DATA_DIR / "app.sqlite3"), kiwoom, lambda ds: runner, lambda *x: None, parse_text)
    s = a.settings()
    L = [f"자동 매매 성적표 · {dt.date.today()} · {'켜짐' if s['enabled'] else '꺼짐'} · "
         f"{'완전 자동' if s['mode'] == 'auto' else '승인 모드'} · 데이터 {panel.dates.max()}"]
    for c in a.compare():
        x, e = c["actual"], c["expect"]
        L.append("")
        x_ = next((y for y in s["strategies"] if y["id"] == c["strategy_id"]), {})
        size = (f"계좌 {x_.get('pct', 10):g}% (1회 최대 {x_.get('max_per_trade', 0) / 1e4:,.0f}만)"
                if x_.get("size_mode") == "pct" else f"1회 {c['per_trade'] / 1e4:,.0f}만")
        L.append(f"■ {c['name']} · {size} · {'켜짐' if x_.get('enabled', True) else '꺼짐'}")
        if e:
            L.append(f"  기대(백테스트 {e['since'][:4]}~, {e['n']:,}건): 1건당 {e['avg']:+.2f}% · 수익 비율 {e['win']:.0f}%")
        if not x:
            L.append("  아직 전략대로 청산한 거래 없음")
            continue
        L.append(f"  실제(비용 차감): {x['n']}건 · 1건당 {x['avg']:+.2f}% · 수익 비율 {x['win']:.0f}% · "
                 f"실현 {x['pnl'] / 1e4:+,.0f}만 · 최대 낙폭 {x['mdd'] / 1e4:,.0f}만 · 최장 연속 손실 {x['lose_streak']}")
        if c["band"]:
            L.append(f"  정상 범위: 1건당 {c['band'][0]:+.1f}% ~ {c['band'][1]:+.1f}% · 수익 비율 "
                     f"{c['win_band'][0]:.0f}~{c['win_band'][1]:.0f}% · 판정 {c['verdict']}")
    pos = a.positions()
    act = [p for p in pos if p["status"] != "closed"]
    closed = [p for p in pos if p["status"] == "closed"][:40]
    L += ["", f"보유 {len(act)}종목: " + ", ".join(f"{p['name']}({p['entry_date'][5:]})" for p in act)]
    L += ["", "최근 청산 (최대 40건, 수수료·세금 전)"]
    names = {x["id"]: x["name"] for x in a.view()["strategies"]}
    first = s["strategies"][0]["id"] if s["strategies"] else None  # 여러 전략 전에 산 것은 첫 전략
    for p in closed:
        L.append(f"  {p['exit_date']} {names.get(p['strategy_id'] or first, '?')[:8]:<8} {p['name'][:10]:<10} "
                 f"{p['exit_reason']:<8} {p['ret']:+.2f}%")
    report = "\n".join(L)
    log(report)
    return report
