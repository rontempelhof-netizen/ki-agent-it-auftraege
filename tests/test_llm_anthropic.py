"""Anthropic-Adapter mit gemocktem SDK-Client (kein Netzwerk, kein API-Key)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from src.config import LLMSettings
from src.llm.anthropic_provider import REFUSAL_FALLBACK_BETA, AnthropicProvider
from src.llm.provider import (
    LLMConnectionError,
    LLMProviderError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMRequest,
    LLMServerError,
    LLMTimeoutError,
    LLMTruncatedOutputError,
)
from src.llm.prompts import analysis_json_schema

REQUEST = LLMRequest(system="SYSTEM", user="USER", json_schema=analysis_json_schema(), max_output_tokens=16000)
HTTP_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def message(text: str = '{"summary": "x"}', stop_reason: str = "end_turn", **extra: Any) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        model=extra.get("model", "claude-opus-5-5"),
        stop_reason=stop_reason,
        stop_details=extra.get("stop_details"),
        usage=SimpleNamespace(input_tokens=1200, output_tokens=350, cache_read_input_tokens=0, cache_creation_input_tokens=0),
        _request_id="req_test_1",
    )


class FakeMessages:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeClient:
    def __init__(self, result: Any) -> None:
        self.messages = FakeMessages(result)
        self.beta = SimpleNamespace(messages=FakeMessages(result))


def provider_for(result: Any, **settings: Any) -> tuple[AnthropicProvider, FakeClient]:
    client = FakeClient(result)
    return AnthropicProvider(LLMSettings(**settings), client=client), client


def test_default_request_uses_regular_endpoint_without_fallback():
    provider, client = provider_for(message())

    provider.complete(REQUEST)

    (call,) = client.messages.calls
    assert "betas" not in call and "fallbacks" not in call
    assert client.beta.messages.calls == []


def test_request_uses_structured_output_effort_and_fallback_when_enabled():
    provider, client = provider_for(message(), refusal_fallback=True)

    provider.complete(REQUEST)

    (call,) = client.beta.messages.calls
    assert call["model"] == "claude-opus-5-5"
    assert call["max_tokens"] == 16000
    assert call["system"] == "SYSTEM"
    assert call["messages"] == [{"role": "user", "content": "USER"}]
    assert call["output_config"]["effort"] == "medium"
    assert call["output_config"]["format"] == {"type": "json_schema", "schema": REQUEST.json_schema}
    assert call["betas"] == [REFUSAL_FALLBACK_BETA] and call["fallbacks"] == "default"
    assert "thinking" not in call  # Opus 5.5: Thinking immer aktiv, Steuerung über effort
    assert client.messages.calls == []


def test_model_and_effort_are_configurable():
    provider, client = provider_for(message(), model="claude-sonnet-5-5", effort="low")

    provider.complete(REQUEST)

    (call,) = client.messages.calls
    assert call["model"] == "claude-sonnet-5-5" and call["output_config"]["effort"] == "low"
    assert "betas" not in call and client.beta.messages.calls == []


def test_response_mapping():
    provider, _ = provider_for(message(text=json.dumps({"summary": "ok"}), model="claude-opus-4-8"))

    response = provider.complete(REQUEST)

    assert json.loads(response.text) == {"summary": "ok"}  # nur Text-Blöcke, kein Thinking
    assert response.model == "claude-opus-4-8"  # tatsächliches (ggf. Fallback-)Modell
    assert response.usage.input_tokens == 1200 and response.usage.output_tokens == 350
    assert response.request_id == "req_test_1"


def test_refusal_is_mapped():
    provider, _ = provider_for(message(stop_reason="refusal", stop_details=SimpleNamespace(category="cyber")))

    with pytest.raises(LLMRefusalError, match="cyber"):
        provider.complete(REQUEST)


def test_truncated_output_is_mapped():
    provider, _ = provider_for(message(stop_reason="max_tokens"))

    with pytest.raises(LLMTruncatedOutputError):
        provider.complete(REQUEST)


def _status(cls: type[anthropic.APIStatusError], code: int) -> anthropic.APIStatusError:
    return cls("Fehler", response=httpx2.Response(code, request=HTTP_REQUEST), body=None)


@pytest.mark.parametrize(
    ("error", "expected", "retryable"),
    [
        (anthropic.APITimeoutError(request=HTTP_REQUEST), LLMTimeoutError, True),
        (anthropic.APIConnectionError(request=HTTP_REQUEST), LLMConnectionError, True),
        (_status(anthropic.RateLimitError, 429), LLMRateLimitError, True),
        (_status(anthropic.InternalServerError, 500), LLMServerError, True),
        (_status(anthropic.APIStatusError, 529), LLMServerError, True),
        (_status(anthropic.AuthenticationError, 401), LLMProviderError, False),
        (_status(anthropic.BadRequestError, 400), LLMProviderError, False),
    ],
)
def test_sdk_errors_are_mapped(error, expected, retryable):
    provider, _ = provider_for(error)

    with pytest.raises(expected) as exc:
        provider.complete(REQUEST)

    assert exc.value.retryable is retryable
    assert isinstance(exc.value.__cause__, anthropic.AnthropicError)
