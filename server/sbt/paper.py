"""모의투자 (모투): 카테고리별로 담은 종목을 실제 이후 시세로 전략 규칙대로 체결·청산하고 계좌를 계산한다.

- 체결·청산은 검증과 같은 엔진(event.simulate)으로 매번 다시 계산한다 (저녁 임시값 → 아침 공식 값이 들어오면 자동 반영).
- 계좌: '내 선택' 거래만 총 자본금 한도 안에서 산다. 현금이 1회 매수금보다 적으면 그 거래는 건너뛴다.
- 자동 담기: 카테고리 조건의 신호를 전부 '전체 신호'로 담아, 내 선택과 비교한다.
- 소급: 매수 시점(다음 거래일 장 시작)이 지난 뒤에 담은 거래는 통계에서 기본으로 뺀다.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import numpy as np

from .engine import event
from .engine.run import Runner
from .engine.spec import MARKET_LABEL, Spec, parse_text, to_text
from .engine.stats import _r

KST = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")


def canon_text(spec: Spec) -> str:
    """조건 비교용 글 (이름·기간 끝 제외)."""
    s = spec.model_copy(update={"name": "", "universe": spec.universe.model_copy(update={"end": None})})
    return to_text(s)


def book_spec(book: dict) -> Spec:
    return parse_text(book["spec_text"])


# ---------------------------------------------------------------- 소급 판정
def _next_weekday(d: dt.date) -> dt.date:
    d += dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def entry_start(dataset: str, signal_date: str, entry_type: str) -> dt.datetime:
    """매수가 처음 일어날 수 있는 시각 (이 시각 이후에 담으면 소급)."""
    d = dt.date.fromisoformat(signal_date)
    if dataset == "us":
        if entry_type == "close":
            return dt.datetime.combine(d, dt.time(16, 0), NY)
        return dt.datetime.combine(_next_weekday(d), dt.time(9, 30), NY)
    if entry_type == "close":
        return dt.datetime.combine(d, dt.time(15, 30), KST)
    return dt.datetime.combine(_next_weekday(d), dt.time(9, 0), KST)


def is_retro(dataset: str, signal_date: str, entry_type: str, now: dt.datetime | None = None) -> bool:
    now = now or dt.datetime.now(KST)
    start = entry_start(dataset, signal_date, entry_type)
    # 종가 매수는 신호가 장 마감 후에야 확정되므로 그날 밤까지 담으면 정상으로 본다
    if entry_type == "close":
        start = start.replace(hour=23, minute=59)
    return now > start


# ---------------------------------------------------------------- 신호 (자동 담기)
def signals_since(runner: Runner, spec: Spec, since: str) -> list[dict]:
    """since 이후 신호 전부 (code, name, market, signal_date)."""
    from .engine import dsl
    from .engine.run import universe_mask

    p = runner.panel
    s = spec.model_copy(update={"universe": spec.universe.model_copy(update={"end": None})})
    with runner.lock:
        cond = dsl.evaluate(s.signal, p, runner.ev)
        m = cond & universe_mask(p, s) & (p.dates >= np.datetime64(since, "D"))
    idx = np.flatnonzero(m)
    idx = event.dedupe(idx, p, s.dedupe_days)
    return [{"code": str(p.codes[i]), "name": str(p.names[i]),
             "market": MARKET_LABEL.get(p.markets[i], p.markets[i]), "signal_date": str(p.dates[i])} for i in idx]


# ---------------------------------------------------------------- 거래 계산
def simulate_trades(runner: Runner, spec: Spec, trades: list[dict]) -> list[dict]:
    """trades: [{code, signal_date, manual_exit_date?}] → 거래마다 상태·수익."""
    p = runner.panel
    rows, keep = [], []
    for k, t in enumerate(trades):
        r = p.rows_for(t["code"])
        hit = r[p.dates[r] == np.datetime64(t["signal_date"], "D")] if len(r) else r
        if len(hit):
            rows.append(int(hit[0]))
            keep.append(k)
    out: list[dict] = [{"status": "nodata", "label": "시세 없음"} for _ in trades]
    if not rows:
        return out
    s = spec
    sig = np.array(rows, dtype=np.int64)
    with runner.lock:
        tr = event.simulate(p, sig, s, runner._limit(s), runner._fx(s), runner._exit(s))
    c = p.cols
    for j, k in enumerate(keep):
        i = int(tr.sig[j])
        t = trades[k]
        g_last = int(p.gend[i])
        fr = int(tr.fill_row[j])
        er = int(tr.exit_row_c[j])
        oc = int(tr.outcome_c[j])
        res = {
            "limit": _r(tr.limit[j]),
            "signal_close": _r(c["close"][i]),
            "fill_date": str(p.dates[fr]) if fr >= 0 else None,
            "fill_price": _r(tr.fill_price[j]) if fr >= 0 else None,
            "exit_date": None, "exit_price": None, "ret": None, "days": None,
            "last_date": str(p.dates[g_last]), "last_close": _r(c["close"][g_last]),
            "mfe": _r(tr.mfe[j]), "mae": _r(tr.mae[j]),
        }
        manual = t.get("manual_exit_date")
        if fr >= 0 and manual and (oc == event.OPEN or (er >= 0 and str(p.dates[er]) > manual)):
            # 수동 정리: 그날 종가에 판 것으로
            mr = [r for r in range(fr, g_last + 1) if str(p.dates[r]) <= manual]
            if mr:
                r = mr[-1]
                px = float(c["close"][r])
                cost = s.costs
                ret = (px * (1 - (cost.sell_fee_pct + cost.sell_tax_pct + cost.slippage_pct) / 100)
                       / (tr.fill_price[j] * (1 + (cost.buy_fee_pct + cost.slippage_pct) / 100)) - 1) * 100
                res.update(status="closed", label="수동 정리", outcome="수동정리", exit_date=str(p.dates[r]),
                           exit_price=_r(px), ret=_r(ret), days=r - fr)
                out[k] = res
                continue
        if fr >= 0:
            if oc == event.OPEN:
                res.update(status="holding", label="보유 중", outcome="진행중",
                           ret_now=_r((c["close"][g_last] / tr.fill_price[j] - 1) * 100), days=g_last - fr)
            else:
                res.update(status="closed", label=event.OUTCOME_LABEL[oc], outcome=event.OUTCOME_LABEL[oc],
                           exit_date=str(p.dates[er]) if er >= 0 else None, exit_price=_r(tr.exit_price_c[j]),
                           ret=_r(tr.ret_c[j]), days=int(tr.days_c[j]) if tr.days_c[j] >= 0 else None)
        else:
            after = g_last - i  # 신호일 뒤로 지난 거래일 수 (= 주문을 걸어 둔 날 수)
            vd = int(s.entry.valid_days)
            res["waited"] = after
            if s.entry.type == "limit" and after < vd:
                res.update(status="pending", label=f"주문 대기 · 남은 {vd - after}일", outcome="대기", left=vd - after)
            elif s.entry.type == "next_open" and after == 0:
                res.update(status="pending", label="다음 거래일 시가 매수", outcome="대기", left=1)
            else:
                last_try = i + (vd if s.entry.type == "limit" else 1)
                res.update(status="nofill", label="미체결 · 주문 끝남", outcome="미체결",
                           expire_date=str(p.dates[min(last_try, g_last)]))
        out[k] = res
    return out


# ---------------------------------------------------------------- 계좌
def account(trades: list[dict], cash_events: list[dict], dates: list[str], closes: dict) -> dict:
    """'내 선택' 거래로 자본금 한도 계좌를 돌린다.

    trades: 계산 결과가 붙은 거래 (picked 만, 담은 순서). 거래에 qty·invested·skip 을 채운다.
    cash_events: [{date, kind: init|deposit|withdraw|per_trade, amount}]
    closes: {trade_id: {date: close}} (평가용)
    """
    # 처음 정한 자본금·1회 매수금은 날짜와 상관없이 맨 처음부터 적용 (소급으로 넣은 옛 거래도 계산되게)
    first_pt = min((e["id"] for e in cash_events if e["kind"] == "per_trade"), default=None)
    evs = sorted(({**e, "date": "0000-00-00"} if e["kind"] == "init" or e["id"] == first_pt else e
                  for e in cash_events), key=lambda e: (e["date"], e["id"]))
    per_trade = 0.0
    cash = 0.0
    contributed = 0.0
    ei = 0

    def apply_cash(upto: str):
        nonlocal ei, cash, contributed, per_trade
        while ei < len(evs) and evs[ei]["date"] <= upto:
            e = evs[ei]
            if e["kind"] in ("init", "deposit"):
                cash += e["amount"]
                contributed += e["amount"]
            elif e["kind"] == "withdraw":
                cash -= e["amount"]
                contributed -= e["amount"]
            elif e["kind"] == "per_trade":
                per_trade = e["amount"]
            ei += 1

    fills = sorted([t for t in trades if t.get("fill_date")], key=lambda t: (t["fill_date"], t["id"]))
    for t in fills:
        t["skip"] = None
    all_days = sorted(set(dates) | {t["fill_date"] for t in fills} | {t["exit_date"] for t in fills if t.get("exit_date")})
    held: dict[int, dict] = {}
    curve = []
    fi = 0
    for d in all_days:
        apply_cash(d)
        # 그날 판 것을 먼저 현금으로
        for tid in [k for k, t in held.items() if t.get("exit_date") == d]:
            t = held.pop(tid)
            t["proceeds"] = t["invested"] * (1 + (t["ret"] or 0) / 100)
            t["pnl"] = t["proceeds"] - t["invested"]
            cash += t["proceeds"]
        while fi < len(fills) and fills[fi]["fill_date"] == d:
            t = fills[fi]
            fi += 1
            if t.get("retro") and not t.get("include_retro"):
                t["skip"] = "소급 (계좌 제외)"
                continue
            price = t["fill_price"]
            qty = int(per_trade // price) if price and per_trade > 0 else 0
            if qty <= 0:
                t["skip"] = "1주 가격이 1회 매수금보다 큼" if per_trade > 0 else "1회 매수금 없음"
                continue
            invested = qty * price
            if invested > cash + 1e-6:
                t["skip"] = "자금 부족"
                continue
            t["qty"] = qty
            t["invested"] = invested
            cash -= invested
            if t.get("exit_date") == d:  # 당일 매수·매도
                t["proceeds"] = invested * (1 + (t["ret"] or 0) / 100)
                t["pnl"] = t["proceeds"] - invested
                cash += t["proceeds"]
            else:
                held[t["id"]] = t
        if d in dates:
            mv = 0.0
            for t in held.values():
                px = closes.get(t["id"], {}).get(d)
                mv += t["qty"] * (px if px else t["fill_price"])
            curve.append({"date": d, "equity": round(cash + mv, 2), "cash": round(cash, 2),
                          "contributed": round(contributed, 2)})
    apply_cash("9999-12-31")
    mv = 0.0
    for t in held.values():
        px = t.get("last_close") or t["fill_price"]
        t["value"] = t["qty"] * px
        t["pnl"] = t["value"] - t["invested"]
        mv += t["value"]
    equity = cash + mv
    peak, mdd = 0.0, 0.0
    for p in curve:
        # 입출금 효과를 빼고 낙폭을 보려고 '자본 대비 비율'로
        r = p["equity"] / p["contributed"] if p["contributed"] else 1
        peak = max(peak, r)
        mdd = min(mdd, (r / peak - 1) * 100 if peak else 0)
    return {
        "cash": round(cash, 2), "positions_value": round(mv, 2), "equity": round(equity, 2),
        "contributed": round(contributed, 2), "pnl": round(equity - contributed, 2),
        "ret": _r((equity / contributed - 1) * 100) if contributed else None,
        "per_trade": per_trade, "holding": len(held), "mdd": _r(mdd), "curve": curve,
    }


def group_stats(ts: list[dict]) -> dict:
    done = [t for t in ts if t.get("status") == "closed" and t.get("ret") is not None]
    rets = [t["ret"] for t in done]
    hold = [t for t in ts if t.get("status") == "holding"]
    return {
        "n": len(ts), "closed": len(done), "holding": len(hold),
        "pending": sum(1 for t in ts if t.get("status") == "pending"),
        "nofill": sum(1 for t in ts if t.get("status") == "nofill"),
        "win_rate": _r(sum(1 for r in rets if r > 0) / len(rets) * 100, 1) if rets else None,
        "avg_ret": _r(sum(rets) / len(rets)) if rets else None,
        "best": _r(max(rets)) if rets else None, "worst": _r(min(rets)) if rets else None,
        "avg_now": _r(sum(t["ret_now"] for t in hold if t.get("ret_now") is not None) / len(hold)) if hold else None,
        "avg_days": _r(sum(t["days"] or 0 for t in done) / len(done), 1) if done else None,
        "outcomes": {o: sum(1 for t in done if t["outcome"] == o) for o in sorted({t["outcome"] for t in done})},
    }


def view(runner: Runner, book: dict, trades: list[dict], cash_events: list[dict], with_curve: bool = True) -> dict:
    spec = book_spec(book)
    sims = simulate_trades(runner, spec, trades)
    p = runner.panel
    for t, s in zip(trades, sims):
        t.update(s)
    picked = [t for t in trades if t["picked"]]
    # 평가용 종가 (보유 기간)
    closes: dict[int, dict] = {}
    start = min([t["fill_date"] for t in picked if t.get("fill_date")] + [e["date"] for e in cash_events] or [None]) \
        if (picked or cash_events) else None
    dates: list[str] = []
    if start:
        ud = np.unique(p.dates)
        dates = [str(d) for d in ud[ud >= np.datetime64(start, "D")]]
        if with_curve:
            for t in picked:
                if not t.get("fill_date"):
                    continue
                r = p.rows_for(t["code"])
                end = t.get("exit_date") or "9999-12-31"
                sel = r[(p.dates[r] >= np.datetime64(t["fill_date"], "D")) & (p.dates[r] <= np.datetime64(end[:10], "D"))]
                closes[t["id"]] = {str(p.dates[i]): float(p.cols["close"][i]) for i in sel}
    acc = account(picked, cash_events, dates, closes)
    if not with_curve:
        acc.pop("curve")
    fair = [t for t in trades if not t.get("retro")]
    groups = {"picked": group_stats([t for t in fair if t["picked"]])}
    if book["auto"]:
        groups["all"] = group_stats([t for t in fair if t["auto"]])
        groups["unpicked"] = group_stats([t for t in fair if t["auto"] and not t["picked"]])
        a, b = groups["picked"], groups["all"]
        groups["edge"] = {
            "win_rate": _r(a["win_rate"] - b["win_rate"], 1) if a["win_rate"] is not None and b["win_rate"] is not None else None,
            "avg_ret": _r(a["avg_ret"] - b["avg_ret"]) if a["avg_ret"] is not None and b["avg_ret"] is not None else None,
        }
    groups["retro"] = sum(1 for t in trades if t.get("retro"))
    return {"account": acc, "groups": groups, "trades": trades, "as_of": str(p.dates.max()) if len(p.dates) else None}


# ---------------------------------------------------------------- 소식 (체결·미체결·청산)
def news(book: dict, trades: list[dict], as_of: str | None, fmt_price) -> list[dict]:
    """내 선택 거래에서 날짜가 있는 사건을 뽑는다. trades 는 simulate 결과가 붙은 것."""
    out = []
    for t in trades:
        if not t.get("picked"):
            continue
        base = {"book_id": book["id"], "book": book["name"], "trade_id": t["id"], "code": t["code"], "name": t["name"],
                "retro": bool(t.get("retro"))}
        if t.get("fill_date"):
            out.append({**base, "date": t["fill_date"], "kind": "fill",
                        "text": f"매수 체결 {fmt_price(t.get('fill_price'))}"})
        if t.get("status") == "closed" and t.get("exit_date"):
            r = t.get("ret")
            out.append({**base, "date": t["exit_date"], "kind": "exit", "ret": r,
                        "text": f"매도 · {t.get('label')} {'' if r is None else f'{r:+.2f}%'}"})
        if t.get("status") == "pending" and as_of and t.get("waited", 0) >= 1:
            out.append({**base, "date": as_of, "kind": "wait",
                        "text": f"미체결 · 지정가 {fmt_price(t.get('limit'))}에 안 닿음 · 남은 {t.get('left')}일"})
        if t.get("status") == "nofill" and t.get("expire_date"):
            out.append({**base, "date": t["expire_date"], "kind": "expire",
                        "text": "미체결로 주문 끝남 (기간 안에 매수가에 닿지 않음)"})
    return out
