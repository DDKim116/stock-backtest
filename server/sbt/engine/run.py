"""검증 실행 진입점."""
from __future__ import annotations

import time

import numpy as np

from ..data.panel import Panel
from . import dsl, event, stats
from .spec import Spec, expand, validate

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
        return event.simulate(self.panel, sig, spec, self._limit(spec))

    def baseline(self, spec: Spec) -> dict:
        key = spec.universe.model_dump_json() + spec.entry.model_dump_json() + spec.exit.model_dump_json() \
            + spec.costs.model_dump_json()
        if key in self._base_cache:
            return self._base_cache[key]
        uni = np.flatnonzero(self._universe(spec))
        rng = np.random.default_rng(42)
        sample = np.sort(rng.choice(uni, size=min(BASELINE_SAMPLE, len(uni)), replace=False)) if len(uni) else uni
        t = event.simulate(self.panel, sample, spec, self._limit(spec))
        s = stats.summarize(t, spec)
        res = {"n": s["n_complete"], "success_rate": s["success_rate"], "avg_ret": s["avg_ret"],
               "fill_rate": s["fill_rate"]}
        if len(self._base_cache) > 50:
            self._base_cache.clear()
        self._base_cache[key] = res
        return res

    def run(self, spec: Spec, with_trades: bool = True) -> dict:
        errs = validate(spec)
        if errs:
            raise SpecError(errs)
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
        bd = stats.breakdowns(t, self.panel, s)
        out = {
            "kind": "single",
            "spec": s.model_dump(mode="json"),
            "summary": summ,
            "baseline": base,
            "breakdowns": bd,
            "checks": stats.checks(summ, base, bd, t, self.panel, self.dataset),
            "dataset": self.dataset,
        }
        if with_trades:
            out["trades"] = stats.trade_rows(t, self.panel)
            out["trades_truncated"] = len(t) > len(out["trades"])
        out["elapsed"] = round(time.time() - t0, 2)
        return out
