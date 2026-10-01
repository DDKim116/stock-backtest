"""AI 기능 테스트 — 실제 API 대신 가짜 응답을 넣어 변환·검증·비용 처리를 확인한다."""
import importlib
import os

import pytest
from fastapi.testclient import TestClient

from sbt.ai import client as ai_client
from sbt.ai import service
from sbt.ai.client import Usage


def ai_out(**spec_over):
    spec = {
        "signal": "거래량 >= 거래량(1) * 5 AND 등락률 >= 12",
        "entry_type": "limit", "entry_price": "(시가 + 종가) / 2", "valid_days": [5],
        "target_pct": [3], "target_basis": "high", "stop_pct": [], "max_days": [10],
        "markets": ["KOSPI", "KOSDAQ"], "start": "2018-01-01", "end": "",
        "exclude_spac": True, "exclude_preferred": True, "dedupe_days": 0,
    }
    spec.update(spec_over)
    return {
        "is_backtest": True,
        "reply": "거래량 급증 + 12% 급등일 이후 중간값 매수로 이해했습니다.",
        "interpretation": ["신호일: ..."],
        "assumptions": [{"item": "500% 상승", "chosen": "전일의 5배", "alternatives": ["전일의 6배로 해줘"]}],
        "unsupported": [],
        "spec": spec,
    }


def fake_calls(outputs):
    calls = []

    def fake(model, system, user, schema, **kw):
        calls.append(user)
        return outputs[len(calls) - 1], Usage(model, 1000, 2000, 3000, 0)

    return fake, calls


def test_usage_cost():
    u = Usage("claude-opus-5-5", 1_000_000, 1_000_000, 0, 1_000_000)
    assert u.cost_usd == pytest.approx(4 + 20 + 0.2)


def test_translate_maps_lists(monkeypatch):
    fake, _ = fake_calls([ai_out(target_pct=[3, 5], stop_pct=[])])
    monkeypatch.setattr(service, "call_json", fake)
    r = service.translate("질문", None, "claude-opus-5-5")
    assert r["ok"]
    s = r["spec"]
    assert s.exit.target_pct == [3.0, 5.0] and s.exit.stop_pct is None and s.entry.valid_days == 5
    assert s.universe.end is None
    assert "[신호]" in r["text"]


def test_translate_retries_on_invalid(monkeypatch):
    fake, calls = fake_calls([ai_out(signal="등락률 >= 12%"), ai_out()])
    monkeypatch.setattr(service, "call_json", fake)
    r = service.translate("질문", None, "claude-opus-5-5")
    assert r["ok"] and len(calls) == 2
    assert "실패했습니다" in calls[1]
    assert r["usage"].input_tokens == 2000  # 두 번 호출 합산


def test_translate_followup_keeps_current(monkeypatch):
    fake, calls = fake_calls([ai_out()])
    monkeypatch.setattr(service, "call_json", fake)
    cur = "[신호]\n등락률 >= 10\n[청산]\n목표 = 5\n"
    service.translate("6배로 바꿔줘", cur, "claude-opus-5-5", ["처음 질문"])
    assert "현재 조건:" in calls[0] and "수정 요청: 6배로 바꿔줘" in calls[0] and "처음 질문" in calls[0]


def test_not_backtest(monkeypatch):
    out = ai_out()
    out["is_backtest"] = False
    out["reply"] = "검증할 매매 조건을 말씀해 주세요."
    fake, _ = fake_calls([out])
    monkeypatch.setattr(service, "call_json", fake)
    r = service.translate("안녕", None, "claude-opus-5-5")
    assert r["ok"] is False and "매매 조건" in r["reply"]


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    d = tmp_path_factory.mktemp("data")
    os.environ["SBT_DATA_DIR"] = str(d)
    os.environ["ANTHROPIC_API_KEY"] = "test-key"
    from sbt import config
    importlib.reload(config)
    from sbt.data import build, synthetic
    synthetic.write(d / "demo", n_codes=30, start="2018-01-01", end="2020-12-31")
    build.build(d / "demo", log=lambda *a: None)
    from sbt.api import app as app_mod
    importlib.reload(app_mod)
    yield TestClient(app_mod.app), app_mod
    os.environ.pop("ANTHROPIC_API_KEY", None)


def test_api_translate_and_explain(client, monkeypatch):
    c, app_mod = client
    fake, _ = fake_calls([ai_out()])
    monkeypatch.setattr(service, "call_json", fake)
    r = c.post("/api/ai/translate", json={"question": "거래량 500% 12% 상승 후 중간값 매수 3% 비율?"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] and j["spec"]["signal"].startswith("거래량") and j["usage"]["cost_krw"] > 0

    explain_out = {
        "verdict": "평소보다 낫지만 표본이 적습니다",
        "points": ["성공률 80%"],
        "risks": [],
        "suggestions": [
            {"title": "손절 추가", "why": "손실 제한", "text": j["text"].replace("손절 = 없음", "손절 = 5")},
            {"title": "잘못된 제안", "why": "-", "text": "[신호]\n가격 > 3"},
        ],
    }
    monkeypatch.setattr(service, "call_json", lambda *a, **k: (explain_out, Usage("claude-opus-5-5", 10, 10)))
    r = c.post("/api/ai/explain", json={"dataset": "demo", "spec": j["spec"]})
    assert r.status_code == 200, r.text
    e = r.json()
    assert len(e["suggestions"]) == 1 and "손절 = 5" in e["suggestions"][0]["text"]

    st = c.get("/api/ai/status").json()
    assert st["enabled"] and st["month_count"] == 2


def test_api_budget_limit(client, monkeypatch):
    c, _ = client
    c.put("/api/ai/settings", json={"monthly_limit_usd": 0})
    r = c.post("/api/ai/translate", json={"question": "아무거나"})
    assert r.status_code == 402
    c.put("/api/ai/settings", json={"monthly_limit_usd": 20})
    assert c.put("/api/ai/settings", json={"model": "gpt"}).status_code == 422


def test_disabled_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ai_client.AIError):
        ai_client.call_json("claude-opus-5-5", "s", "u", {})
