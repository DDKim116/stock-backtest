"""검증 실행 진입점."""
from __future__ import annotations

import threading
import time

import numpy as np

from ..data.panel import Panel
from . import dsl, event, explore, stats
from .stats import _r
from .spec import MARKET_LABEL, Spec, expand, validate

BASELINE_SAMPLE = 200_000


class SpecError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def universe_mask(panel: Panel, spec: Spec) -> np.ndarray:
    u = spec.universe
    m = np.isin(panel.markets, u.markets)
    m &= panel.dates >= np.datetime64(u.start, "D")
    if u.end:
        m &= panel.dates <= np.datetime64(u.end, "D")
    if u.exclude_spac:
        m &= ~panel.is_spac
    if u.exclude_preferred:
        m &= ~panel.is_preferred
    m &= panel.pos > 0  # 첫 거래일은 전일 값이 없어 제외
    return m


class Runner:
    """같은 데이터 위에서 여러 번 실행할 때 지표 계산을 재사용한다."""

    def __init__(self, panel: Panel, dataset: dict):
        self.panel = panel
        self.dataset = dataset
        self.ev = dsl.Evaluator(panel)
        self._uni_cache: dict[str, np.ndarray] = {}
        self._base_cache: dict[str, dict] = {}
        # 지표 캐시를 여러 요청이 동시에 건드리지 않도록 (탐색이 도는 중에도 검증은 조합 사이사이에 끼어 실행됨)
        self.lock = threading.RLock()

    def _universe(self, spec: Spec) -> np.ndarray:
        key = spec.universe.model_dump_json()
        if key not in self._uni_cache:
            if len(self._uni_cache) > 20:
                self._uni_cache.clear()
            self._uni_cache[key] = universe_mask(self.panel, spec)
        return self._uni_cache[key]

    def _limit(self, spec: Spec):
        if spec.entry.type != "limit":
            return None
        return dsl.evaluate(spec.entry.price, self.panel, self.ev).astype(np.float64)

    def trades(self, spec: Spec) -> event.Trades:
        uni = self._universe(spec)
        cond = dsl.evaluate(spec.signal, self.panel, self.ev)
        if cond.dtype != bool:
            raise SpecError(["신호는 비교식(>=, < 등)이어야 합니다. 예: 등락률 >= 12"])
        sig = np.flatnonzero(cond & uni)
        sig = event.dedupe(sig, self.panel, spec.dedupe_days)
        return event.simulate(self.panel, sig, spec, self._limit(spec), self._fx(spec))

    def _fx(self, spec: Spec):
        if not spec.fx_krw:
            return None
        if self.panel.fx is None:
            raise SpecError(["이 데이터에는 환율이 없어 원화 환산을 할 수 없습니다 (미국 데이터에서만 가능)"])
        return self.panel.fx

    def _check_markets(self, spec: Spec) -> None:
        have = self.dataset.get("markets")
        if have and not set(spec.universe.markets) & set(have):
            label = ", ".join(MARKET_LABEL.get(m, m) for m in have)
            raise SpecError([f"이 데이터셋의 시장은 {label} 입니다. [대상] 시장을 바꿔 주세요."])

    def baseline(self, spec: Spec) -> dict:
        key = spec.universe.model_dump_json() + spec.entry.model_dump_json() + spec.exit.model_dump_json() \
            + spec.costs.model_dump_json() + str(spec.fx_krw)
        if key in self._base_cache:
            return self._base_cache[key]
        uni = np.flatnonzero(self._universe(spec))
        rng = np.random.default_rng(42)
        sample = np.sort(rng.choice(uni, size=min(BASELINE_SAMPLE, len(uni)), replace=False)) if len(uni) else uni
        t = event.simulate(self.panel, sample, spec, self._limit(spec), self._fx(spec))
        s = stats.summarize(t, spec)
        res = {"n": s["n_complete"], "success_rate": s["success_rate"], "avg_ret": s["avg_ret"],
               "fill_rate": s["fill_rate"]}
        if len(self._base_cache) > 50:
            self._base_cache.clear()
        self._base_cache[key] = res
        return res

    def run(self, spec: Spec, with_trades: bool = True) -> dict:
        with self.lock:
            return self._run(spec, with_trades)

    def _run(self, spec: Spec, with_trades: bool = True) -> dict:
        errs = validate(spec)
        if errs:
            raise SpecError(errs)
        self._check_markets(spec)
        t0 = time.time()
        variants = expand(spec)
        if len(variants) > 1:
            rows = []
            for params, s in variants:
                t = self.trades(s)
                summ = stats.summarize(t, s)
                base = self.baseline(s)
                rows.append({"params": params, "spec": s.model_dump(mode="json"), "summary": summ,
                             "baseline": base})
            return {"kind": "sweep", "variants": rows, "elapsed": round(time.time() - t0, 2),
                    "dataset": self.dataset}

        s = variants[0][1]
        t = self.trades(s)
        summ = stats.summarize(t, s)
        base = self.baseline(s)
        if summ["success_rate"] is not None and base["success_rate"] is not None:
            summ["edge_pp"] = round(summ["success_rate"] - base["success_rate"], 1)
        bd = stats.breakdowns(t, self.panel, s, self.dataset.get("currency", "KRW"))
        ds = {**self.dataset, "fx_applied": s.fx_krw}
        out = {
            "kind": "single",
            "spec": s.model_dump(mode="json"),
            "summary": summ,
            "baseline": base,
            "breakdowns": bd,
            "checks": stats.checks(summ, base, bd, t, self.panel, ds),
            "dataset": ds,
        }
        if with_trades:
            out["trades"] = stats.trade_rows(t, self.panel)
            out["trades_truncated"] = len(t) > len(out["trades"])
        out["elapsed"] = round(time.time() - t0, 2)
        return out


    # ------------------------------------------------------------ 오늘 신호 (검색기)
    def scan(self, spec: Spec, days: int | None = None, limit: int = 500) -> dict:
        with self.lock:
            return self._scan(spec, days, limit)

    def _scan(self, spec: Spec, days: int | None = None, limit: int = 500) -> dict:
        """최근 days 거래일 동안 신호가 난 종목과 지금 주문·보유 상태.

        지정가 매수면 기본으로 유효기간만큼 거슬러 올라가 아직 주문이 살아 있는 신호까지 보여 준다.
        """
        errs = validate(spec)
        if errs:
            raise SpecError(errs)
        self._check_markets(spec)
        variants = expand(spec)
        if len(variants) > 1:
            raise SpecError(["검색에서는 숫자 자리에 값을 하나만 넣어 주세요 (비교표는 검증에서만 됩니다)"])
        s = variants[0][1]
        s = s.model_copy(update={"universe": s.universe.model_copy(update={"end": None})})
        p = self.panel
        if days is None:
            days = int(s.entry.valid_days) if s.entry.type == "limit" else 1
        days = max(1, min(int(days), 20))
        tdays = np.unique(p.dates)
        last = tdays[-1]
        since = tdays[max(0, len(tdays) - days)]
        cond = dsl.evaluate(s.signal, p, self.ev)
        if cond.dtype != bool:
            raise SpecError(["신호는 비교식(>=, < 등)이어야 합니다. 예: 등락률 >= 12"])
        m = cond & np.isin(p.markets, s.universe.markets) & (p.dates >= since) & (p.pos > 0)
        if s.universe.exclude_spac:
            m &= ~p.is_spac
        if s.universe.exclude_preferred:
            m &= ~p.is_preferred
        sig = np.flatnonzero(m)
        t = event.simulate(p, sig, s, self._limit(s), self._fx(s))
        day_idx = {d: k for k, d in enumerate(tdays)}
        c = p.cols
        rows = []
        for k in range(len(t)):
            i = int(t.sig[k])
            after = len(tdays) - 1 - day_idx[p.dates[i]]  # 신호일 뒤로 지난 거래일 수
            g_last = int(p.gend[i])  # 이 종목의 마지막 행
            fr = int(t.fill_row[k])
            er = int(t.exit_row_c[k])
            outcome = event.OUTCOME_LABEL[int(t.outcome_c[k])]
            ret_now = None
            if s.entry.type == "close":
                fr = i
            if fr >= 0:
                if er >= 0 and outcome != "진행중":
                    status, kind = f"청산됨 · {outcome}", "closed"
                else:
                    status, kind = "보유 중", "holding"
                    ret_now = _r((c["close"][g_last] / t.fill_price[k] - 1) * 100) if np.isfinite(t.fill_price[k]) else None
            elif s.entry.type == "limit":
                left = int(s.entry.valid_days) - after
                status, kind = (f"주문 대기 · 남은 {left}일", "pending") if left > 0 else ("미체결 · 기간 끝남", "expired")
            elif s.entry.type == "next_open":
                status, kind = ("다음 거래일 시가 매수", "pending") if after == 0 else ("미체결", "expired")
            else:
                status, kind = "신호일 종가 매수", "holding"
            prev = c["close"][i - 1]
            rows.append({
                "code": p.codes[i], "name": p.names[i], "market": MARKET_LABEL.get(p.markets[i], p.markets[i]),
                "signal_date": str(p.dates[i]), "close": _r(c["close"][i]),
                "change": _r((c["close"][i] / prev - 1) * 100) if prev > 0 else None,
                "volume": _r(c["volume"][i], 0), "value": _r(c["value"][i], 0) if "value" in c else None,
                "mktcap": _r(c["mktcap"][i], 0) if "mktcap" in c else None,
                "limit": _r(t.limit[k]), "fill_date": str(p.dates[fr]) if fr >= 0 else None,
                "fill_price": _r(t.fill_price[k]) if fr >= 0 else None,
                "last_close": _r(c["close"][g_last]), "ret_now": ret_now,
                "status": status, "kind": kind, "outcome": outcome,
                "exit_date": str(p.dates[er]) if er >= 0 and kind == "closed" else None,
                "exit_price": _r(t.exit_price_c[k]) if kind == "closed" else None,
                "ret": _r(t.ret_c[k]) if kind == "closed" else None,
                "mfe": _r(t.mfe[k]), "mae": _r(t.mae[k]),
            })
        order = {"pending": 0, "holding": 1, "closed": 2, "expired": 3}
        rows.sort(key=lambda r: r["signal_date"], reverse=True)
        rows.sort(key=lambda r: order[r["kind"]])
        return {"as_of": str(last), "since": str(since), "days": days, "count": len(rows),
                "rows": rows[:limit], "truncated": len(rows) > limit,
                "spec": s.model_dump(mode="json"), "dataset": self.dataset}

    # ------------------------------------------------------------ 전략 탐색
    def explore_plan(self, spec: Spec, split: str | None = None) -> tuple[explore.Plan, np.datetime64]:
        errs = validate(spec)
        if errs:
            raise SpecError(errs)
        self._check_markets(spec)
        try:
            plan = explore.make_plan(spec)
        except ValueError as e:
            raise SpecError([str(e)])
        if len(plan.combos) > explore.MAX_COMBOS:
            raise SpecError([f"조합이 {len(plan.combos)}개입니다. {explore.MAX_COMBOS}개 이하로 줄여 주세요"])
        if split:
            cut = np.datetime64(split, "D")
        else:
            uni = self._universe(spec)
            cut = explore.default_split(self.panel.dates[uni])
        return plan, cut

    def explore(self, spec: Spec, split: str | None = None, progress=None, cancelled=lambda: False) -> dict:
        plan, cut = self.explore_plan(spec, split)
        t0 = time.time()
        rows = []
        for k, combo in enumerate(plan.combos):
            if cancelled():
                break
            s = explore.apply(spec, plan, combo)
            with self.lock:
                t = self.trades(s)
                ev = explore.evaluate(t, s, self.panel.dates[t.sig], cut)
                base = self.baseline(s)
            rows.append({"combo": list(combo), "spec": s.model_dump(mode="json"), "baseline": base, **ev})
            if progress:
                progress(k + 1, len(plan.combos))
        explore.judge(rows, plan)
        dates = self.panel.dates[self._universe(spec)]
        return {
            "axes": [{"key": a.key, "kind": a.kind, "values": a.values} for a in plan.axes],
            "user_defined": plan.user_defined,
            "notes": plan.notes,
            "split": str(cut),
            "period": [str(dates.min()), str(dates.max())] if len(dates) else None,
            "elapsed": round(time.time() - t0, 1),
            **explore.summarize_run(rows),
            "dataset": self.dataset,
        }
