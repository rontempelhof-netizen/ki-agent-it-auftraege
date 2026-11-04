"""Regression tests for Anthropic's structured-output union-type grammar limit."""

from __future__ import annotations

from types import SimpleNamespace

from src.config import LLMSettings
from src.llm.anthropic_provider import (
    MAX_STRUCTURED_UNION_PARAMETERS,
    AnthropicProvider,
    _union_parameter_count,
)
from src.llm.prompts import analysis_json_schema
from src.llm.provider import LLMRequest


class StubMessages:
    def __init__(self, reply: str = "{}") -> None:
        self.calls: list[dict] = []
        self.reply = reply

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self.reply)],
            model="claude-sonnet-5-5",
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=500, output_tokens=100, cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0),
            _request_id="req-test",
        )


def _provider(messages: StubMessages) -> AnthropicProvider:
    return AnthropicProvider(
        LLMSettings(model="claude-sonnet-5-5", effort="medium"),
        client=SimpleNamespace(messages=messages),
    )


def test_full_real_analysis_schema_exceeds_claude_union_limit() -> None:
    """The existing LeadAnalysis schema must not go to constrained decoding unchanged."""
    assert _union_parameter_count(analysis_json_schema()) > MAX_STRUCTURED_UNION_PARAMETERS


def test_full_schema_uses_prompt_only_json_without_dropping_fields() -> None:
    messages = StubMessages()
    schema = analysis_json_schema()
    req = LLMRequest(system="Regeln für Datenextraktion", user="<auftragstext>Untrusted job</auftragstext>",
                     json_schema=schema, max_output_tokens=1000)

    response = _provider(messages).complete(req)

    args = messages.calls[0]
    assert args["output_config"] == {"effort": "medium"}
    assert "Regeln für Datenextraktion" in args["system"]
    assert "required_certifications" in args["system"]
    assert "budget_min" in args["system"]
    assert args["messages"] == [{"role": "user", "content": req.user}]
    assert response.text == "{}"
    assert response.usage.input_tokens == 500


def test_small_schema_retains_api_enforced_structured_output() -> None:
    messages = StubMessages('{"name": "abc"}')
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    }

    _provider(messages).complete(
        LLMRequest(system="Follow rules", user="Job listing", json_schema=schema, max_output_tokens=1000)
    )

    args = messages.calls[0]
    assert args["system"] == "Follow rules"
    assert args["output_config"] == {
        "effort": "medium",
        "format": {"type": "json_schema", "schema": schema},
    }


def test_union_count_traverses_nested_schema() -> None:
    schema = {"properties": {"a": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                             "b": {"type": ["integer", "null"]},
                             "c": {"type": "array", "items": {"type": ["string", "null"]}}}}
    assert _union_parameter_count(schema) == 3
