"""전략 탐색: 기본 아이디어 주변의 숫자 조합을 자동으로 돌리고, 기간을 나눠 검증해 걸러낸다.

1. 탐색 범위 정하기 (plan)
   - 사용자가 [a, b] 로 값을 적었으면 그 조합만 돈다.
   - 아니면 신호의 기준값(비교·곱하기 뒤의 숫자)을 ±30% 로, 청산(목표·손절·보유기간)을 정해진 후보로 바꿔 본다.
2. 조합마다 '과거 구간'과 '검증 구간'(기본: 기간의 뒤쪽 약 30%)을 따로 집계한다.
3. 걸러내기: 표본 부족, 어느 구간이든 평균 손실, 수익 난 해가 적음, 이웃 조합에서 무너짐(과최적화)
4. 통과한 조합을 두 구간 중 나쁜 쪽 평균 수익률 기준으로 정렬한다.
"""
from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass, field

import numpy as np

from .spec import Spec, _fmt, expand
from .stats import summarize

MAX_COMBOS = 300
MIN_IS = 100  # 과거 구간 최소 표본
MIN_OOS = 30  # 검증 구간 최소 표본
MIN_POS_YEARS = 0.6

# 비교·곱하기 바로 뒤의 숫자 = 기준값 (이평(종가, 20) 의 20 같은 기간은 건드리지 않음)
_THRESH_RE = re.compile(r"(>=|<=|>|<|\*)(\s*)(-?\d+(?:\.\d+)?)([만억조]?)")


def _nice(x: float) -> float:
    a = abs(x)
    if a >= 10:
        return float(round(x))
    if a >= 1:
        return round(x, 1)
    return round(x, 2)


@dataclass
class Axis:
    key: str  # 화면 표시용 이름
    kind: str  # signal / target / stop / days / user
    values: list
    index: int = 0  # signal 기준값 순번


@dataclass
class Plan:
    axes: list[Axis]
    combos: list[tuple]  # 각 축 값의 조합
    user_defined: bool
    notes: list[str] = field(default_factory=list)


def _signal_axes(signal: str) -> list[Axis]:
    axes = []
    for i, m in enumerate(_THRESH_RE.finditer(signal)):
        if i >= 3:
            break
        v = float(m.group(3))
        unit = m.group(4)
        vals = sorted({_nice(v * 0.7), _nice(v), _nice(v * 1.3)}, key=lambda z: z)
        if v < 0:
            vals = sorted(vals, reverse=True)
        label = f"{m.group(1)} {m.group(3)}{unit}"
        axes.append(Axis(key=f"신호 기준값 {i + 1} ({label})", kind="signal", values=vals, index=i))
    return axes


def make_plan(spec: Spec) -> Plan:
    variants = expand(spec)
    if len(variants) > 1:
        # 사용자가 직접 [a, b] 로 범위를 정함
        keys = list(variants[0][0].keys())
        axes = [Axis(key=k, kind="user", values=sorted({p[k] for p, _ in variants})) for k in keys]
        return Plan(axes=axes, combos=[tuple(p[k] for k in keys) for p, _ in variants], user_defined=True)

    axes = _signal_axes(spec.signal)
    tgt = spec.exit.target_pct
    exits = [
        Axis("목표(%)", "target", [3.0, 5.0, 10.0] if tgt is not None else [None, 5.0, 10.0]),
        Axis("손절(%)", "stop", [None, 5.0, 10.0]),
        Axis("보유기간(일)", "days", [5, 10, 20]),
    ]
    notes = []
    n_sig = math.prod(len(a.values) for a in axes) if axes else 1
    if n_sig * 27 > MAX_COMBOS:
        exits = [
            Axis("목표(%)", "target", [3.0, 10.0] if tgt is not None else [None, 10.0]),
            Axis("손절(%)", "stop", [None, 7.0]),
            Axis("보유기간(일)", "days", [10]),
        ]
        notes.append("기준값이 많아 청산 후보를 줄였습니다.")
    axes += exits
    if not _signal_axes(spec.signal):
        notes.append("신호에 바꿔 볼 기준값(>=, < , * 뒤의 숫자)이 없어 청산 조건만 바꿔 봅니다.")
    combos = list(itertools.product(*[a.values for a in axes]))
    return Plan(axes=axes, combos=combos, user_defined=False, notes=notes)


def apply(spec: Spec, plan: Plan, combo: tuple) -> Spec:
    if plan.user_defined:
        # expand 의 순서와 같은 조합을 찾는다
        for params, s in expand(spec):
            if tuple(params[a.key] for a in plan.axes) == combo:
                return s
        raise ValueError("조합을 찾지 못했습니다")
    sig_vals = {a.index: v for a, v in zip(plan.axes, combo) if a.kind == "signal"}
    k = iter(range(1000))

    def rep(m):
        i = next(k)
        if i in sig_vals:
            return f"{m.group(1)}{m.group(2)}{_fmt(sig_vals[i])}{m.group(4)}"
        return m.group(0)

    signal = _THRESH_RE.sub(rep, spec.signal)
    ex = spec.exit.model_dump()
    for a, v in zip(plan.axes, combo):
        if a.kind == "target":
            ex["target_pct"] = v
        elif a.kind == "stop":
            ex["stop_pct"] = v
        elif a.kind == "days":
            ex["max_days"] = v
    return spec.model_copy(update={"signal": signal, "exit": spec.exit.model_validate(ex)})


def default_split(dates: np.ndarray) -> np.datetime64:
    """기간의 앞 약 70% 를 과거 구간으로. 연초로 맞춘다."""
    d = np.unique(dates)
    cut = d[int(len(d) * 0.7)]
    year = cut.astype("datetime64[Y]")
    return year.astype("datetime64[D]")


def _brief(s: dict) -> dict:
    return {k: s[k] for k in ("n_complete", "success_rate", "avg_ret", "median_ret", "fill_rate", "n_stop")}


def evaluate(t, spec: Spec, sig_dates: np.ndarray, split: np.datetime64) -> dict:
    is_m = sig_dates < split
    oos_m = ~is_m
    years = sig_dates.astype("datetime64[Y]").astype(int) + 1970
    pos, tot = 0, 0
    for y in np.unique(years):
        s = summarize(t, spec, years == y)
        if s["n_complete"] >= 10 and s["avg_ret"] is not None:
            tot += 1
            pos += s["avg_ret"] > 0
    return {
        "all": _brief(summarize(t, spec)),
        "is": _brief(summarize(t, spec, is_m)),
        "oos": _brief(summarize(t, spec, oos_m)),
        "pos_years": round(pos / tot, 2) if tot else None,
        "n_years": tot,
    }


def judge(rows: list[dict], plan: Plan) -> None:
    """표본·손실·연도·이웃 조합 기준으로 통과 여부와 이유를 적는다."""
    idx = {tuple(r["combo"]): r for r in rows}
    for r in rows:
        reasons = []
        i, o = r["is"], r["oos"]
        if i["n_complete"] < MIN_IS:
            reasons.append(f"과거 구간 표본 {i['n_complete']}건 (최소 {MIN_IS})")
        if o["n_complete"] < MIN_OOS:
            reasons.append(f"검증 구간 표본 {o['n_complete']}건 (최소 {MIN_OOS})")
        if i["avg_ret"] is None or i["avg_ret"] <= 0:
            reasons.append("과거 구간 평균 손실")
        if o["avg_ret"] is None or o["avg_ret"] <= 0:
            reasons.append("검증 구간 평균 손실")
        if r["pos_years"] is not None and r["pos_years"] < MIN_POS_YEARS:
            reasons.append(f"수익 난 해 {round(r['pos_years'] * 100)}%")
        # 이웃 조합: 한 축만 한 칸 옮긴 조합들의 과거 구간 평균 수익률
        neigh = []
        for k, a in enumerate(plan.axes):
            vals = a.values
            j = vals.index(r["combo"][k])
            for jj in (j - 1, j + 1):
                if 0 <= jj < len(vals):
                    c = list(r["combo"])
                    c[k] = vals[jj]
                    n = idx.get(tuple(c))
                    if n and n["is"]["avg_ret"] is not None and n["is"]["n_complete"] >= MIN_IS // 2:
                        neigh.append(n["is"]["avg_ret"])
        r["neighbor_avg"] = round(float(np.median(neigh)), 2) if neigh else None
        if neigh and i["avg_ret"] and i["avg_ret"] > 0 and np.median(neigh) <= 0:
            reasons.append("숫자를 조금만 바꿔도 손실 (과최적화 의심)")
        r["reasons"] = reasons
        r["passed"] = not reasons
        both = [x for x in (i["avg_ret"], o["avg_ret"]) if x is not None]
        r["score"] = min(both) if len(both) == 2 else None


def summarize_run(rows: list[dict]) -> dict:
    passed = [r for r in rows if r["passed"]]
    passed.sort(key=lambda r: r["score"], reverse=True)
    reasons: dict[str, int] = {}
    for r in rows:
        for x in r["reasons"]:
            key = re.sub(r"\s*\d+%?건?.*$", "", x) if "표본" in x or "수익 난 해" in x else x
            reasons[key] = reasons.get(key, 0) + 1
    # 탈락했지만 참고할 만한 상위 (검증 구간 기준)
    rest = [r for r in rows if not r["passed"] and r["oos"]["avg_ret"] is not None]
    rest.sort(key=lambda r: r["oos"]["avg_ret"], reverse=True)
    return {
        "n_total": len(rows),
        "n_passed": len(passed),
        "fail_reasons": sorted(reasons.items(), key=lambda kv: -kv[1]),
        "passed": passed[:30],
        "near_miss": rest[:10],
    }
