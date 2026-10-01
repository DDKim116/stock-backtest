"""Claude API 호출과 비용 계산."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

import anthropic

# 달러 / 100만 토큰 (input, output, cache_write, cache_read)
PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 5.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 2.5, 0.20),
    "claude-haiku-4-5": (1.0, 5.0, 1.25, 0.10),
    # 서버 측 대체 모델(거절 시)로 쓰일 수 있는 모델
    "claude-opus-5": (5.0, 25.0, 6.25, 0.50),
    "claude-opus-4-8": (5.0, 25.0, 6.25, 0.50),
    "claude-sonnet-5": (2.0, 10.0, 2.5, 0.20),
}
MODELS = [
    {"id": "claude-opus-5-5", "label": "Opus 5.5 (정확도 우선, 기본)"},
    {"id": "claude-sonnet-5-5", "label": "Sonnet 5.5 (약 절반 가격)"},
    {"id": "claude-haiku-4-5", "label": "Haiku 4.5 (가장 저렴, 해석 정확도 낮음)"},
]
DEFAULT_MODEL = "claude-opus-5-5"


class AIError(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


@dataclass
class Usage:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write: int = 0
    cache_read: int = 0

    @property
    def cost_usd(self) -> float:
        pi, po, pw, pr = PRICES.get(self.model, PRICES[DEFAULT_MODEL])
        return (self.input_tokens * pi + self.output_tokens * po + self.cache_write * pw
                + self.cache_read * pr) / 1e6

    def add(self, other: "Usage") -> "Usage":
        return Usage(other.model, self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens,
                     self.cache_write + other.cache_write, self.cache_read + other.cache_read)


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(timeout=180.0, max_retries=2)
    return _client


def call_json(model: str, system: str, user: str, schema: dict, effort: str = "medium",
              max_tokens: int = 12000) -> tuple[dict, Usage]:
    """JSON 형식 출력을 강제해서 한 번 호출한다."""
    if not enabled():
        raise AIError("AI 기능이 꺼져 있습니다. 서버 .env 에 ANTHROPIC_API_KEY 를 넣어 주세요.", 503)
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        # 지시문은 고정이라 캐시해 두면 두 번째 질문부터 입력 비용이 크게 줄어든다
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
    }
    if model != "claude-haiku-4-5":
        kwargs["output_config"]["effort"] = effort
        # 안전 분류기가 드물게 거절하면 서버가 권장 모델로 다시 실행한다
        kwargs["betas"] = ["server-side-fallback-2026-07-01"]
        kwargs["fallbacks"] = "default"
    try:
        resp = client().beta.messages.create(**kwargs)
    except anthropic.AuthenticationError:
        raise AIError("Anthropic API 키가 올바르지 않습니다.", 503)
    except anthropic.PermissionDeniedError as e:
        raise AIError(f"Anthropic API 권한 오류: {e.message}", 503)
    except anthropic.RateLimitError:
        raise AIError("AI 요청이 너무 많습니다. 잠시 후 다시 시도하세요.", 429)
    except anthropic.BadRequestError as e:
        msg = e.message
        if "credit" in msg.lower() or "balance" in msg.lower():
            raise AIError("Anthropic 계정의 충전 잔액이 부족합니다. 콘솔에서 크레딧을 충전하세요.", 402)
        raise AIError(f"AI 요청 오류: {msg}", 502)
    except anthropic.APIStatusError as e:
        raise AIError(f"AI 서버 오류 ({e.status_code}). 잠시 후 다시 시도하세요.", 502)
    except anthropic.APIConnectionError:
        raise AIError("AI 서버에 연결할 수 없습니다.", 502)

    u = resp.usage
    usage = Usage(
        model=resp.model or model,
        input_tokens=u.input_tokens or 0,
        output_tokens=u.output_tokens or 0,
        cache_write=getattr(u, "cache_creation_input_tokens", 0) or 0,
        cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
    )
    if resp.stop_reason == "refusal":
        raise AIError("AI가 이 요청에 답하지 않았습니다. 질문을 바꿔 보세요.", 422)
    if resp.stop_reason == "max_tokens":
        raise AIError("AI 응답이 너무 길어 잘렸습니다. 질문을 나눠 보세요.", 502)
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        return json.loads(text), usage
    except json.JSONDecodeError:
        raise AIError("AI 응답을 해석하지 못했습니다. 다시 시도하세요.", 502)
