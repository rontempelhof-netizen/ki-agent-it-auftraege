"""LLM-Schicht: anbieterneutraler Provider-Vertrag, Prompt, Validierung und Analyse-Service.

Konkrete Anbieter werden nur über ``create_provider`` geladen; der übrige Code hängt
ausschließlich von ``LLMProvider`` ab.
"""

from __future__ import annotations

from src.config import LLMSettings
from src.llm.fake import FakeLLMProvider
from src.llm.provider import LLMError, LLMProvider, LLMRequest, LLMResponse, LLMUsage
from src.llm.service import AnalysisFailedError, AnalysisResult, LeadAnalysisService


def create_provider(settings: LLMSettings) -> LLMProvider:
    if settings.provider == "fake":
        if settings.fake_responses_file:
            return FakeLLMProvider.from_responses_file(settings.fake_responses_file)
        return FakeLLMProvider(model="fake-model")
    from src.llm.anthropic_provider import AnthropicProvider  # lazy: SDK nur bei Bedarf laden

    return AnthropicProvider(settings)


__all__ = [
    "AnalysisFailedError",
    "AnalysisResult",
    "FakeLLMProvider",
    "LLMError",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "LLMUsage",
    "LeadAnalysisService",
    "create_provider",
]
