"""Anbieterneutraler Vertrag für LLM-Aufrufe.

Die übrige Anwendung kennt nur diese Typen. Konkrete Anbieter (z. B. Anthropic)
übersetzen ihre Antworten und Fehler in diese Klassen.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class LLMRequest(BaseModel):
    system: str
    user: str
    json_schema: dict[str, Any]
    """JSON-Schema der erwarteten Antwort (Structured Output, falls vom Anbieter unterstützt)."""
    max_output_tokens: int = Field(gt=0)


class LLMUsage(BaseModel):
    """Token-/Kosteninformationen; jedes Feld ist optional, da nicht jeder Anbieter sie liefert."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cost_usd: float | None = None

    def __add__(self, other: LLMUsage) -> LLMUsage:
        """Summiert Felder; ``None`` nur, wenn beide Seiten den Wert nicht kennen."""
        merged = {}
        for field in LLMUsage.model_fields:
            a, b = getattr(self, field), getattr(other, field)
            merged[field] = None if a is None and b is None else (a or 0) + (b or 0)
        return LLMUsage.model_validate(merged)


class LLMResponse(BaseModel):
    text: str
    model: str
    """Tatsächlich antwortendes Modell (kann bei Fallback vom angefragten abweichen)."""
    stop_reason: str | None = None
    usage: LLMUsage | None = None
    request_id: str | None = None


# --- Fehler ------------------------------------------------------------------


class LLMError(Exception):
    """Basisklasse. ``retryable`` kennzeichnet technische, vorübergehende Fehler."""

    retryable: bool = False
    reason: str = "provider_error"


class LLMTimeoutError(LLMError):
    retryable = True
    reason = "timeout"


class LLMRateLimitError(LLMError):
    retryable = True
    reason = "rate_limit"


class LLMConnectionError(LLMError):
    retryable = True
    reason = "connection"


class LLMServerError(LLMError):
    retryable = True
    reason = "server_error"


class LLMTruncatedOutputError(LLMError):
    """Antwort wegen Token-Limit abgeschnitten (technischer Fehler)."""

    retryable = True
    reason = "truncated"


class LLMRefusalError(LLMError):
    """Modell hat die Anfrage abgelehnt. Keine Wiederholung (inhaltliche Entscheidung)."""

    reason = "refusal"


class LLMProviderError(LLMError):
    """Nicht behebbarer Fehler (z. B. Authentifizierung, ungültige Anfrage, Konfiguration)."""

    reason = "provider_error"


class LLMProvider(ABC):
    name: str

    @property
    @abstractmethod
    def model(self) -> str:
        """Angefragtes Modell (für Nachvollziehbarkeit)."""

    @abstractmethod
    def complete(self, request: LLMRequest) -> LLMResponse:
        """Führt einen Aufruf aus. Fehler werden als ``LLMError``-Unterklassen gemeldet."""
