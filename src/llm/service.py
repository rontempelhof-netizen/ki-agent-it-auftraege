"""LeadAnalysisService: LeadCandidate -> validierte LeadAnalysis (ohne Score).

Retry-Politik: Wiederholt wird nur bei technischen Fehlern (Timeout, Rate Limit,
Verbindungs-/Serverfehler, abgeschnittene Antwort) und strukturell ungültigen
Antworten (kein JSON, Schemafehler, unzulässige Entscheidungsfelder). Eine gültige
Analyse wird nie wiederholt, unabhängig von ihrem Inhalt. Ablehnungen (Refusal)
und nicht behebbare Providerfehler werden ebenfalls nicht wiederholt.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from pydantic import BaseModel

from src.config import LLMPricing, LLMSettings
from src.domain.models import LeadAnalysis, LeadCandidate
from src.llm.prompts import (
    RETRY_NOTE,
    analysis_json_schema,
    build_system_prompt,
    build_user_message,
    grounding_text,
    prompt_version_for,
)
from src.llm.provider import LLMError, LLMProvider, LLMRequest, LLMUsage
from src.llm.validation import OutputValidationError, ground_analysis, parse_analysis_output

logger = logging.getLogger(__name__)


class AnalysisResult(BaseModel):
    analysis: LeadAnalysis
    prompt_version: str
    provider: str
    model: str
    """Modell, das die gültige Antwort geliefert hat."""
    attempts: int
    usage: LLMUsage | None = None
    """Über alle Versuche summiert; None, wenn der Provider keine Angaben liefert."""
    grounding_issues: list[str] = []
    """Vom LLM gelieferte, im Quelltext nicht belegte Fakten, die verworfen wurden."""
    input_truncated: bool = False


class AnalysisFailedError(Exception):
    def __init__(self, reason: str, attempts: int, errors: list[str], usage: LLMUsage | None) -> None:
        super().__init__(f"LLM-Analyse fehlgeschlagen ({reason}) nach {attempts} Versuch(en): {errors[-1] if errors else ''}")
        self.reason = reason
        self.attempts = attempts
        self.errors = errors
        self.usage = usage


def estimate_cost(usage: LLMUsage | None, pricing: LLMPricing | None) -> LLMUsage | None:
    """Ergänzt eine Kostenschätzung, sofern Tokens und Preise bekannt sind."""
    if usage is None or pricing is None or usage.cost_usd is not None:
        return usage
    if usage.input_tokens is None or usage.output_tokens is None:
        return usage
    cost = (usage.input_tokens * pricing.input_per_mtok + usage.output_tokens * pricing.output_per_mtok) / 1_000_000
    return usage.model_copy(update={"cost_usd": round(cost, 6)})


class LeadAnalysisService:
    def __init__(
        self,
        provider: LLMProvider,
        settings: LLMSettings,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._provider = provider
        self._settings = settings
        self._sleep = sleep
        self._system_prompt = build_system_prompt(settings.analyst_profile)
        self._schema = analysis_json_schema()
        self.prompt_version = prompt_version_for(settings.analyst_profile)

    def analyze(self, candidate: LeadCandidate) -> AnalysisResult:
        user_message, truncated = build_user_message(candidate, self._settings.max_input_chars)
        if truncated:
            logger.warning("llm_input_truncated", extra={"source": candidate.source, "source_id": candidate.source_id})

        errors: list[str] = []
        usage: LLMUsage | None = None
        retry_note = ""
        max_attempts = self._settings.max_attempts

        for attempt in range(1, max_attempts + 1):
            request = LLMRequest(
                system=self._system_prompt,
                user=user_message + retry_note,
                json_schema=self._schema,
                max_output_tokens=self._settings.max_output_tokens,
            )
            started = time.perf_counter()
            try:
                response = self._provider.complete(request)
            except LLMError as exc:
                errors.append(f"{exc.reason}: {exc}")
                self._log_call(candidate, attempt, None, started, outcome=exc.reason)
                if not exc.retryable or attempt == max_attempts:
                    raise AnalysisFailedError(exc.reason, attempt, errors, usage) from exc
                self._sleep(self._settings.retry_backoff_seconds * attempt)
                continue

            call_usage = estimate_cost(response.usage, self._settings.pricing)
            usage = call_usage if usage is None else (usage + call_usage if call_usage else usage)
            try:
                analysis = parse_analysis_output(response.text)
            except OutputValidationError as exc:
                errors.append(str(exc))
                self._log_call(candidate, attempt, call_usage, started, outcome=exc.kind, model=response.model)
                if attempt == max_attempts:
                    raise AnalysisFailedError(exc.kind, attempt, errors, usage) from exc
                retry_note = RETRY_NOTE.format(problem=exc.summary)
                continue

            self._log_call(candidate, attempt, call_usage, started, outcome="ok", model=response.model)
            analysis, issues = ground_analysis(analysis, grounding_text(candidate))
            if issues:
                logger.warning("llm_ungrounded_facts", extra={"source_id": candidate.source_id, "issues": issues})
            return AnalysisResult(
                analysis=analysis,
                prompt_version=self.prompt_version,
                provider=self._provider.name,
                model=response.model,
                attempts=attempt,
                usage=usage,
                grounding_issues=issues,
                input_truncated=truncated,
            )
        raise AssertionError("unreachable")  # pragma: no cover

    def _log_call(
        self,
        candidate: LeadCandidate,
        attempt: int,
        usage: LLMUsage | None,
        started: float,
        outcome: str,
        model: str | None = None,
    ) -> None:
        """Protokolliert Metadaten und Kosten; niemals Prompt- oder Antwortinhalte."""
        logger.info(
            "llm_call",
            extra={
                "provider": self._provider.name,
                "model": model or self._provider.model,
                "prompt_version": self.prompt_version,
                "source": candidate.source,
                "source_id": candidate.source_id,
                "attempt": attempt,
                "outcome": outcome,
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "input_tokens": usage.input_tokens if usage else None,
                "output_tokens": usage.output_tokens if usage else None,
                "cost_usd": usage.cost_usd if usage else None,
            },
        )
