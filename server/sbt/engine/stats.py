"""검증 결과 통계와 자동 점검."""
from __future__ import annotations

import numpy as np

from ..data.panel import Panel
from .event import NO_FILL, OPEN, OUTCOME_LABEL, STOP, TARGET, TIMEOUT, Trades
from .spec import MARKET_LABEL, Spec


def _r(x, nd=2):
    if x is None:
        return None
    x = float(x)
    return None if not np.isfinite(x) else round(x, nd)


def _rate(a: int, b: int):
    return _r(a / b * 100, 1) if b else None


def summarize(t: Trades, spec: Spec, mask: np.ndarray | None = None) -> dict:
    """mask 로 일부 거래만 집계할 수 있다 (연도별 등)."""
    if mask is None:
        mask = np.ones(len(t), dtype=bool)
    oc, oo = t.outcome_c[mask], t.outcome_o[mask]
    n_sig = int(mask.sum())
    filled = oc != NO_FILL
    done_c = filled & (oc != OPEN)
    done_o = filled & (oo != OPEN)
    has_target = spec.exit.target_pct is not None
    rc, ro = t.ret_c[mask], t.ret_o[mask]
    if has_target:
        win_c = int((oc == TARGET).sum())
        win_o = int((oo == TARGET).sum())
    else:
        win_c = int((done_c & (rc > 0)).sum())
        win_o = int((done_o & (ro > 0)).sum())
    n_done = int(done_c.sum())
    uncertain = int((done_c & (oc != oo)).sum())
    days = t.days_c[mask][oc == TARGET]
    return {
        "n_signals": n_sig,
        "n_filled": int(filled.sum()),
        "fill_rate": _rate(int(filled.sum()), n_sig),
        "n_complete": n_done,
        "n_open": int((filled & (oc == OPEN)).sum()),
        "n_target": int((oc == TARGET).sum()),
        "n_stop": int((oc == STOP).sum()),
        "n_timeout": int((oc == TIMEOUT).sum()),
        "success_rate": _rate(win_c, n_done),
        "success_rate_opt": _rate(win_o, int(done_o.sum())),
        "n_uncertain": uncertain,
        "avg_ret": _r(np.nanmean(rc[done_c])) if n_done else None,
        "median_ret": _r(np.nanmedian(rc[done_c])) if n_done else None,
        "avg_ret_opt": _r(np.nanmean(ro[done_o])) if done_o.any() else None,
        "profit_rate": _rate(int((rc[done_c] > 0).sum()), n_done),
        "avg_mfe": _r(np.nanmean(t.mfe[mask][done_c])) if n_done else None,
        "avg_mae": _r(np.nanmean(t.mae[mask][done_c])) if n_done else None,
        "avg_days_to_target": _r(np.mean(days), 1) if len(days) else None,
    }


PRICE_BUCKETS = {
    "KRW": [(0, 1000, "1천원 미만"), (1000, 5000, "1천~5천"), (5000, 10000, "5천~1만"),
            (10000, 50000, "1만~5만"), (50000, 200000, "5만~20만"), (200000, np.inf, "20만 이상")],
    "USD": [(0, 1, "$1 미만"), (1, 5, "$1~5"), (5, 20, "$5~20"), (20, 100, "$20~100"),
            (100, 500, "$100~500"), (500, np.inf, "$500 이상")],
}


def breakdowns(t: Trades, panel: Panel, spec: Spec, currency: str = "KRW") -> dict:
    sig = t.sig
    out: dict[str, list] = {}
    years = panel.dates[sig].astype("datetime64[Y]").astype(int) + 1970
    out["year"] = [{"key": str(y), **summarize(t, spec, years == y)} for y in np.unique(years)]
    mk = panel.markets[sig]
    out["market"] = [{"key": MARKET_LABEL.get(m, m), **summarize(t, spec, mk == m)} for m in np.unique(mk)]
    px = panel.df["close_raw"].to_numpy()[sig] if "close_raw" in panel.df else panel.cols["close"][sig]
    rows = []
    for lo, hi, label in PRICE_BUCKETS.get(currency, PRICE_BUCKETS["KRW"]):
        m = (px >= lo) & (px < hi)
        if m.any():
            rows.append({"key": label, **summarize(t, spec, m)})
    out["price"] = rows
    return out


def checks(summary: dict, baseline: dict | None, bd: dict, t: Trades, panel: Panel, dataset: dict) -> list[dict]:
    out = []

    def add(level, text):
        out.append({"level": level, "text": text})

    if dataset.get("synthetic"):
        add("danger", "가상(데모) 데이터로 계산한 결과입니다. 실제 시장과 무관합니다.")
    if dataset.get("survivorship"):
        add("warn", "무료 미국 데이터에는 수집 시작 전에 상장폐지된 종목이 빠져 있어 결과가 실제보다 좋게 나올 수 있습니다.")
    if dataset.get("currency") == "USD":
        add("info", "수익률은 원화 기준(환율 변동 포함)입니다." if dataset.get("fx_applied")
            else "수익률은 달러 기준이며 환율 변동은 반영하지 않았습니다. [옵션] 원화환산 = 예 로 바꿀 수 있습니다.")

    n = summary["n_complete"]
    if n == 0:
        add("danger", "조건을 만족하고 체결까지 된 사례가 없습니다. 조건을 완화해 보세요.")
        return out
    if n < 30:
        add("danger", f"표본 {n}건: 너무 적어 우연일 가능성이 큽니다 (최소 100건 이상 권장).")
    elif n < 100:
        add("warn", f"표본 {n}건: 다소 적습니다. 100건 이상이면 더 믿을 만합니다.")
    else:
        add("good", f"표본 {n:,}건으로 통계적으로 의미 있는 규모입니다.")

    sr = summary["success_rate"]
    if baseline and baseline.get("success_rate") is not None and sr is not None:
        b = baseline["success_rate"]
        d = sr - b
        if d >= 5:
            add("good", f"같은 매수·청산 규칙으로 아무 날이나 샀을 때 성공률 {b}% → 이 조건은 {sr}% (+{d:.1f}%p 우위).")
        elif d > -5:
            add("warn", f"아무 날이나 샀을 때 성공률 {b}%와 큰 차이가 없습니다 ({d:+.1f}%p). 조건의 효과가 약합니다.")
        else:
            add("danger", f"아무 날이나 샀을 때 성공률 {b}%보다 오히려 낮습니다 ({d:+.1f}%p).")

    if sr is not None and sr >= 50 and summary["avg_ret"] is not None and summary["avg_ret"] <= 0:
        add("warn", f"성공률은 {sr}%지만 비용 차감 평균 수익률은 {summary['avg_ret']}%입니다. "
                    "실패할 때 손실이 커서 손절 규칙을 넣어볼 만합니다.")

    unc = summary["n_uncertain"]
    if unc and unc / n >= 0.05:
        add("warn", f"일봉으로는 순서를 알 수 없는 '불확실' 사례가 {unc}건({unc / n * 100:.0f}%)입니다. "
                    f"낙관적으로 보면 성공률 {summary['success_rate_opt']}%입니다.")

    fr = summary["fill_rate"]
    if fr is not None and fr < 30:
        add("info", f"매수 체결률이 {fr}%로 낮습니다. 매수가가 너무 낮거나 유효기간이 짧을 수 있습니다.")

    years = [r for r in bd.get("year", []) if r["n_complete"]]
    if len(years) >= 2 and n >= 20:
        top = max(years, key=lambda r: r["n_complete"])
        share = top["n_complete"] / n
        if share >= 0.35:
            add("warn", f"전체 사례의 {share * 100:.0f}%가 {top['key']}년에 몰려 있습니다. 특정 시기에만 통한 패턴일 수 있습니다.")
        rates = [r["success_rate"] for r in years if r["n_complete"] >= 10 and r["success_rate"] is not None]
        if len(rates) >= 3:
            add("info", f"연도별 성공률(10건 이상인 해): 최저 {min(rates)}% ~ 최고 {max(rates)}%.")

    filled_sig = t.sig[t.outcome_c != NO_FILL]
    if len(filled_sig) >= 20:
        _, counts = np.unique(panel.dates[filled_sig], return_counts=True)
        if counts.max() / len(filled_sig) >= 0.1:
            add("warn", f"하루에 {counts.max()}건이 한꺼번에 나온 날이 있습니다. 시장 전체 급등락일에 결과가 좌우됐을 수 있습니다.")

    if summary["n_open"]:
        add("info", f"최근 신호 {summary['n_open']}건은 보유기간이 끝나지 않아 통계에서 제외했습니다.")
    return out


def trade_rows(t: Trades, panel: Panel, limit: int = 20000) -> list[dict]:
    order = np.argsort(panel.dates[t.sig], kind="stable")[::-1][:limit]
    d = panel.dates
    rows = []
    for k in order:
        i = t.sig[k]
        fr = t.fill_row[k]
        er = t.exit_row_c[k]
        rows.append({
            "code": panel.codes[i],
            "name": panel.names[i],
            "market": MARKET_LABEL.get(panel.markets[i], panel.markets[i]),
            "signal_date": str(d[i]),
            "signal_close": _r(panel.cols["close"][i]),
            "limit": _r(t.limit[k]),
            "fill_date": str(d[fr]) if fr >= 0 else None,
            "fill_price": _r(t.fill_price[k]),
            "outcome": OUTCOME_LABEL[int(t.outcome_c[k])],
            "outcome_opt": OUTCOME_LABEL[int(t.outcome_o[k])],
            "uncertain": bool(t.outcome_c[k] != t.outcome_o[k]),
            "exit_date": str(d[er]) if er >= 0 else None,
            "exit_price": _r(t.exit_price_c[k]),
            "days": int(t.days_c[k]) if t.days_c[k] >= 0 else None,
            "ret": _r(t.ret_c[k]),
            "ret_opt": _r(t.ret_o[k]),
            "mfe": _r(t.mfe[k]),
            "mae": _r(t.mae[k]),
        })
    return rows
