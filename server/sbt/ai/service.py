"""말로 한 질문 → 검증 조건, 결과 → 해설."""
from __future__ import annotations

import datetime as dt
import json

from ..engine.spec import Spec, parse_text, to_text, validate
from . import prompts
from .client import Usage, call_json


def _one(v: list):
    """AI 의 목록 값 → 명세 값 (0개 None / 1개 스칼라 / 여러 개 비교 목록)."""
    if not v:
        return None
    return v[0] if len(v) == 1 else list(v)


def ai_spec_to_spec(a: dict, base: Spec | None, dataset: dict | None = None) -> Spec:
    d = (base.model_dump(mode="json") if base else Spec(signal="종가 > 0").model_dump(mode="json"))
    if not base and dataset:
        d["costs"] = dict(dataset["costs"])
    d["signal"] = a["signal"]
    d["entry"] = {
        "type": a["entry_type"],
        "price": a["entry_price"] or "(시가 + 종가) / 2",
        "valid_days": _one(a["valid_days"]) or 5,
    }
    d["exit"] = {
        "target_pct": _one(a["target_pct"]),
        "target_basis": a["target_basis"],
        "stop_pct": _one(a["stop_pct"]),
        "max_days": _one(a["max_days"]) or 10,
    }
    d["universe"] = {
        "markets": a["markets"] or (dataset or {}).get("markets") or ["KOSPI", "KOSDAQ"],
        "start": a["start"] or "2010-01-01",
        "end": a["end"] or None,
        "exclude_spac": a["exclude_spac"],
        "exclude_preferred": a["exclude_preferred"],
    }
    d["dedupe_days"] = max(0, int(a["dedupe_days"]))
    d["fx_krw"] = bool(a.get("fx_krw")) and (dataset or {}).get("currency") == "USD"
    return Spec.model_validate(d)


def translate(question: str, current_text: str | None, model: str, history: list[str] | None = None,
              dataset: dict | None = None) -> dict:
    base = None
    if current_text:
        try:
            base = parse_text(current_text)
        except Exception:
            base = None
    parts = [f"오늘 날짜: {dt.date.today().isoformat()}"]
    if dataset:
        parts.append(f"사용 중인 데이터: {dataset['label']} / 시장 코드: {', '.join(dataset['markets'])} / "
                     f"통화: {dataset['currency']}")
    if base:
        parts.append("현재 조건:\n" + to_text(base))
    if history:
        parts.append("이전에 사용자가 한 말(순서대로):\n" + "\n".join(f"- {h}" for h in history))
    parts.append(("수정 요청: " if base else "질문: ") + question)
    user = "\n\n".join(parts)

    total: Usage | None = None
    last_err: list[str] = []
    for attempt in range(2):
        prompt = user
        if last_err:
            prompt += ("\n\n앞서 만든 조건이 프로그램 검사에서 실패했습니다. 문법에 맞게 고쳐 주세요:\n- "
                       + "\n- ".join(last_err))
        out, usage = call_json(model, prompts.TRANSLATE_SYSTEM, prompt, prompts.TRANSLATE_SCHEMA)
        total = usage if total is None else total.add(usage)
        if not out.get("is_backtest", True):
            return {"ok": False, "reply": out.get("reply", ""), "usage": total}
        try:
            spec = ai_spec_to_spec(out["spec"], base, dataset)
            last_err = validate(spec)
        except Exception as e:  # 형식 오류
            last_err = [str(e)]
        if not last_err:
            return {
                "ok": True,
                "spec": spec,
                "text": to_text(spec),
                "reply": out.get("reply", ""),
                "interpretation": out.get("interpretation", []),
                "assumptions": out.get("assumptions", []),
                "unsupported": out.get("unsupported", []),
                "usage": total,
            }
    return {"ok": False, "reply": "조건식으로 옮기지 못했습니다: " + "; ".join(last_err), "usage": total}


def _compact_result(res: dict) -> dict:
    """해설에 필요한 부분만 남긴다 (토큰 절약)."""
    if res["kind"] == "sweep":
        return {
            "kind": "sweep",
            "variants": [{"params": v["params"], "summary": v["summary"], "baseline": v["baseline"]}
                         for v in res["variants"]],
            "dataset": res["dataset"].get("label"),
        }
    keep = ("n_signals", "n_filled", "fill_rate", "n_complete", "n_open", "n_target", "n_stop", "n_timeout",
            "success_rate", "success_rate_opt", "n_uncertain", "avg_ret", "median_ret", "profit_rate",
            "avg_mfe", "avg_mae", "avg_days_to_target", "edge_pp")

    def slim(r):
        return {k: r.get(k) for k in ("key",) + keep if k in r}

    return {
        "kind": "single",
        "summary": slim(res["summary"]),
        "baseline": res["baseline"],
        "by_year": [slim(r) for r in res["breakdowns"]["year"]],
        "by_market": [slim(r) for r in res["breakdowns"]["market"]],
        "by_price": [slim(r) for r in res["breakdowns"]["price"]],
        "checks": [c["text"] for c in res["checks"]],
        "dataset": res["dataset"].get("label"),
        "synthetic_data": bool(res["dataset"].get("synthetic")),
    }


def explain(result: dict, spec_text: str, model: str) -> dict:
    user = ("검증 조건:\n" + spec_text + "\n\n결과(JSON):\n"
            + json.dumps(_compact_result(result), ensure_ascii=False))
    out, usage = call_json(model, prompts.EXPLAIN_SYSTEM, user, prompts.EXPLAIN_SCHEMA)
    sugg = []
    for s in out.get("suggestions", []):
        try:
            sp = parse_text(s["text"])
            if validate(sp):
                continue
            sugg.append({"title": s["title"], "why": s["why"], "text": to_text(sp)})
        except Exception:
            continue  # 형식이 틀린 제안은 버린다
    return {
        "verdict": out.get("verdict", ""),
        "points": out.get("points", []),
        "risks": out.get("risks", []),
        "suggestions": sugg,
        "usage": usage,
    }
