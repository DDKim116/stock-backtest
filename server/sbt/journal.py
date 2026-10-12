"""매매일지 계산: 분할 매수·매도 손익(평균단가 방식), 전략대로 했을 때와 비교, 통계."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict


class JournalError(ValueError):
    pass


def _r(x, nd=2):
    return None if x is None else round(float(x), nd)


def position(fills: list[dict], costs: dict, last_close: float | None) -> dict:
    """체결 목록(날짜순) → 보유 수량·평균단가·실현/평가 손익.

    - 평균단가 방식: 매수 수수료까지 원가에 넣고, 매도 때는 평균 원가만큼 덜어 낸다.
    - 매도 금액에서 매도 수수료와 세금을 뺀다. 체결에 fee 를 적으면 그 값을 그대로 쓴다.
    """
    buy_fee = costs.get("buy_fee_pct", 0) / 100
    sell_fee = (costs.get("sell_fee_pct", 0) + costs.get("sell_tax_pct", 0)) / 100
    qty = cost = realized = invested = bought_qty = bought_amt = sold_qty = sold_amt = fees = 0.0
    first_buy = last_sell = None
    for f in fills:
        amt = f["price"] * f["qty"]
        if f["side"] == "buy":
            fee = f["fee"] if f.get("fee") is not None else amt * buy_fee
            qty += f["qty"]
            cost += amt + fee
            invested += amt + fee
            bought_qty += f["qty"]
            bought_amt += amt
            first_buy = first_buy or f["date"]
        else:
            if f["qty"] > qty + 1e-9:
                raise JournalError(f"{f['date']} 매도 수량 {f['qty']:g}이(가) 그때 보유 수량 {qty:g}보다 많습니다")
            fee = f["fee"] if f.get("fee") is not None else amt * sell_fee
            basis = cost / qty * f["qty"]
            realized += amt - fee - basis
            cost -= basis
            qty -= f["qty"]
            sold_qty += f["qty"]
            sold_amt += amt
            last_sell = f["date"]
        fees += fee
    if qty < 1e-9:
        qty, cost = 0.0, 0.0
    closed = qty == 0 and sold_qty > 0
    unrealized = None
    if qty > 0 and last_close:
        unrealized = last_close * qty * (1 - sell_fee) - cost
    pnl = realized + (unrealized or 0)
    return {
        "qty": _r(qty, 4),
        "avg_cost": _r(cost / qty) if qty else None,  # 남은 수량의 주당 원가(수수료 포함)
        "avg_buy": _r(bought_amt / bought_qty) if bought_qty else None,
        "avg_sell": _r(sold_amt / sold_qty) if sold_qty else None,
        "bought_qty": _r(bought_qty, 4),
        "sold_qty": _r(sold_qty, 4),
        "invested": _r(invested),
        "fees": _r(fees),
        "realized": _r(realized),
        "unrealized": _r(unrealized),
        "pnl": _r(pnl),
        "ret": _r(pnl / invested * 100) if invested else None,
        "closed": closed,
        "status": "closed" if closed else ("open" if qty > 0 else "empty"),
        "first_buy": first_buy,
        "last_sell": last_sell,
        "holding_days": (dt.date.fromisoformat(last_sell if closed else dt.date.today().isoformat())
                         - dt.date.fromisoformat(first_buy)).days if first_buy else None,
    }


def compare(pos: dict, strat: dict | None) -> dict | None:
    """내 매매와 전략대로 했을 때 비교."""
    if not strat:
        return None
    out = {"strategy": strat}
    if strat.get("fill_date") is None:
        out["verdict"] = "전략은 미체결 (지정가에 닿지 않음)" if pos["first_buy"] else "전략상 매수 없음"
        out["followed"] = False
        return out
    diffs = []
    if pos["first_buy"] and pos["first_buy"] != strat["fill_date"]:
        diffs.append("매수일이 다름")
    if pos["avg_buy"] and strat.get("fill_price"):
        d = (pos["avg_buy"] / strat["fill_price"] - 1) * 100
        out["buy_diff_pct"] = _r(d)
        if abs(d) > 1:
            diffs.append(f"매수가 {'+' if d > 0 else ''}{d:.1f}%")
    if pos["closed"] and strat.get("exit_date") and strat["outcome"] != "진행중":
        if pos["last_sell"] != strat["exit_date"]:
            diffs.append("매도일이 다름")
            out["sell_timing"] = "늦게 팖" if pos["last_sell"] > strat["exit_date"] else "일찍 팖"
    if pos["ret"] is not None and strat.get("ret") is not None:
        out["ret_diff"] = _r(pos["ret"] - strat["ret"])
    out["followed"] = not diffs
    out["verdict"] = "전략대로 함" if not diffs else ", ".join(diffs)
    return out


def stats(trades: list[dict], strategies: dict[int, dict]) -> dict:
    closed = [t for t in trades if t["pos"]["closed"]]
    opened = [t for t in trades if t["pos"]["status"] == "open"]

    def agg(ts: list[dict]) -> dict:
        rets = [t["pos"]["ret"] for t in ts if t["pos"]["ret"] is not None]
        pnls = [t["pos"]["pnl"] for t in ts if t["pos"]["pnl"] is not None]
        wins = sum(1 for p in pnls if p > 0)
        return {
            "n": len(ts),
            "win_rate": _r(wins / len(pnls) * 100, 1) if pnls else None,
            "avg_ret": _r(sum(rets) / len(rets)) if rets else None,
            "total_pnl": _r(sum(pnls)) if pnls else 0,
            "best": _r(max(rets)) if rets else None,
            "worst": _r(min(rets)) if rets else None,
            "avg_days": _r(sum(t["pos"]["holding_days"] or 0 for t in ts) / len(ts), 1) if ts else None,
        }

    monthly: dict[str, list] = defaultdict(list)
    for t in closed:
        monthly[t["pos"]["last_sell"][:7]].append(t)
    by_strat: dict[str, list] = defaultdict(list)
    for t in closed:
        by_strat[t.get("strategy_name") or "(전략 없음)"].append(t)
    strat_rows = []
    for name, ts in by_strat.items():
        a = agg(ts)
        srets = [t["compare"]["strategy"]["ret"] for t in ts
                 if t.get("compare") and t["compare"]["strategy"].get("ret") is not None]
        a["strategy_avg_ret"] = _r(sum(srets) / len(srets)) if srets else None
        sid = next((t["strategy_id"] for t in ts if t.get("strategy_id")), None)
        summ = (strategies.get(sid) or {}).get("last_summary") if sid else None
        a["backtest_success"] = summ.get("success_rate") if summ else None
        a["backtest_avg_ret"] = summ.get("avg_ret") if summ else None
        strat_rows.append({"name": name, **a})
    strat_rows.sort(key=lambda r: -r["n"])
    followed = [t for t in closed if t.get("compare") and t["compare"].get("followed")]
    deviated = [t for t in closed if t.get("compare") and not t["compare"].get("followed")]
    return {
        "closed": agg(closed),
        "open": {
            "n": len(opened),
            "invested": _r(sum(t["pos"]["invested"] or 0 for t in opened)),
            "unrealized": _r(sum(t["pos"]["unrealized"] or 0 for t in opened)),
        },
        "monthly": [{"month": m, **agg(ts)} for m, ts in sorted(monthly.items())],
        "by_strategy": strat_rows,
        "followed": agg(followed),
        "deviated": agg(deviated),
    }


def to_xlsx(views: list[dict], st: dict, currency: str) -> bytes:
    """매매일지를 엑셀 파일로 (거래·체결·월별·전략별 시트)."""
    import io

    import pandas as pd

    unit = "달러" if currency == "USD" else "원"
    trades = []
    fills = []
    for t in views:
        p, c = t["pos"], t.get("compare") or {}
        s = c.get("strategy") or {}
        trades.append({
            "종목명": t["name"], "코드": t["code"], "시장": t.get("market", ""), "전략": t.get("strategy_name", ""),
            "신호일": t.get("signal_date") or "", "첫 매수일": p.get("first_buy") or "", "마지막 매도일": p.get("last_sell") or "",
            "상태": {"open": "보유 중", "closed": "청산", "error": "확인 필요"}.get(p.get("status"), ""),
            "남은 수량": p.get("qty"), f"평균 매수가({unit})": p.get("avg_buy"), f"평균 매도가({unit})": p.get("avg_sell"),
            f"투입 금액({unit})": p.get("invested"), f"실현 손익({unit})": p.get("realized"),
            f"평가 손익({unit})": p.get("unrealized"), f"손익 합계({unit})": p.get("pnl"), "수익률(%)": p.get("ret"),
            f"수수료·세금({unit})": p.get("fees"), "보유 일수": p.get("holding_days"),
            "전략대로 수익률(%)": s.get("ret"), "전략 비교": c.get("verdict", ""), "메모": t.get("memo", ""),
        })
        for f in t["fills"]:
            fills.append({"종목명": t["name"], "코드": t["code"], "구분": "매수" if f["side"] == "buy" else "매도",
                          "날짜": f["date"], f"가격({unit})": f["price"], "수량": f["qty"],
                          f"금액({unit})": round(f["price"] * f["qty"], 2), "메모": f.get("memo", "")})
    monthly = [{"월": m["month"], "건수": m["n"], "승률(%)": m["win_rate"], "평균 수익률(%)": m["avg_ret"],
                f"손익 합계({unit})": m["total_pnl"]} for m in st["monthly"]]
    strat = [{"전략": r["name"], "건수": r["n"], "승률(%)": r["win_rate"], "내 평균(%)": r["avg_ret"],
              "전략대로 평균(%)": r["strategy_avg_ret"], "백테스트 성공률(%)": r["backtest_success"],
              "백테스트 평균(%)": r["backtest_avg_ret"], f"손익 합계({unit})": r["total_pnl"]} for r in st["by_strategy"]]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for name, rows in (("거래", trades), ("체결", fills), ("월별", monthly), ("전략별", strat)):
            df = pd.DataFrame(rows)
            df.to_excel(w, sheet_name=name, index=False)
            ws = w.sheets[name]
            ws.freeze_panes = "A2"
            for i, col in enumerate(df.columns, start=1):
                width = max([len(str(col))] + [len(str(v)) for v in df[col].head(200)]) if len(df) else len(str(col))
                ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(40, width * 1.6 + 2)
    return buf.getvalue()
