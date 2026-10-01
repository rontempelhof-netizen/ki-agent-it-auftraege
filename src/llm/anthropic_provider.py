"""Adapter für die Claude API (Anthropic SDK). Einziger Ort mit Abhängigkeit vom SDK.

Nutzt Structured Outputs (``output_config.format``), damit die Antwort dem JSON-Schema
entspricht; die Antwort wird anschließend trotzdem strikt per Pydantic validiert.
Wiederholungen steuert der ``LeadAnalysisService``, daher ``max_retries=0`` im SDK.
"""

from __future__ import annotations

from typing import Any

import anthropic

from src.config import LLMSettings
from src.llm.provider import (
    LLMConnectionError,
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMRequest,
    LLMResponse,
    LLMServerError,
    LLMTimeoutError,
    LLMTruncatedOutputError,
    LLMUsage,
)

REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, settings: LLMSettings, client: Any | None = None) -> None:
        self._settings = settings
        # API-Key/Credentials löst das SDK selbst auf (ANTHROPIC_API_KEY o. ä.), nie aus Dateien im Repo.
        self._client = client or anthropic.Anthropic(timeout=settings.timeout_seconds, max_retries=0)

    @property
    def model(self) -> str:
        return self._settings.model

    def complete(self, request: LLMRequest) -> LLMResponse:
        params: dict[str, Any] = {
            "model": self._settings.model,
            "max_tokens": request.max_output_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user}],
            "output_config": {
                "effort": self._settings.effort,
                "format": {"type": "json_schema", "schema": request.json_schema},
            },
        }
        try:
            if self._settings.refusal_fallback:
                response = self._client.beta.messages.create(
                    betas=[REFUSAL_FALLBACK_BETA], fallbacks="default", **params
                )
            else:
                response = self._client.messages.create(**params)
        except anthropic.APITimeoutError as exc:  # Unterklasse von APIConnectionError, daher zuerst
            raise LLMTimeoutError(str(exc)) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMConnectionError(str(exc)) from exc
        except anthropic.RateLimitError as exc:
            raise LLMRateLimitError(str(exc)) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise LLMServerError(f"HTTP {exc.status_code}: {exc.message}") from exc
            raise LLMProviderError(f"HTTP {exc.status_code}: {exc.message}") from exc
        except anthropic.AnthropicError as exc:  # z. B. fehlende Credentials
            raise LLMProviderError(str(exc)) from exc

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise LLMRefusalError(f"Anfrage abgelehnt (Kategorie: {getattr(details, 'category', None)})")
        if response.stop_reason == "max_tokens":
            raise LLMTruncatedOutputError(f"Antwort bei max_tokens={request.max_output_tokens} abgeschnitten")

        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        return LLMResponse(
            text=text,
            model=getattr(response, "model", None) or self._settings.model,
            stop_reason=response.stop_reason,
            usage=_usage(getattr(response, "usage", None)),
            request_id=getattr(response, "_request_id", None),
        )


def _usage(raw: Any) -> LLMUsage | None:
    if raw is None:
        return None
    return LLMUsage(
        input_tokens=getattr(raw, "input_tokens", None),
        output_tokens=getattr(raw, "output_tokens", None),
        cache_read_input_tokens=getattr(raw, "cache_read_input_tokens", None),
        cache_creation_input_tokens=getattr(raw, "cache_creation_input_tokens", None),
    )
