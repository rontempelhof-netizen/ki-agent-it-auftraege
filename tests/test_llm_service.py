from __future__ import annotations

import json
import socket

import pytest

from src.config import DatabaseSettings, LLMPricing, LLMSettings, ScoringSettings
from src.domain.enums import CustomerType, LeadClass, Level, ScopeSize
from src.domain.models import Lead, merge_source_facts
from src.llm import FakeLLMProvider, create_provider
from src.llm.provider import (
    LLMProviderError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMServerError,
    LLMTimeoutError,
    LLMTruncatedOutputError,
    LLMUsage,
)
from src.llm.service import AnalysisFailedError, LeadAnalysisService
from src.scoring.engine import ScoreEngine
from src.storage.database import create_db_engine, create_session_factory, init_db, session_scope
from src.storage.repository import LeadRepository
from tests.llm_fixtures import (
    CERTIFICATION_REQUIRED,
    CERTIFICATION_REQUIRED_OUTPUT,
    CONSULTING_SME,
    CONSULTING_SME_OUTPUT,
    INCOMPLETE,
    INCOMPLETE_HALLUCINATED_OUTPUT,
    INCOMPLETE_HONEST_OUTPUT,
    INJECTION,
    INJECTION_CORRECT_OUTPUT,
    INJECTION_FOOLED_OUTPUT,
    SMALL_CLEAR,
    SMALL_CLEAR_OUTPUT,
    VERY_LARGE,
    VERY_LARGE_OUTPUT,
    llm_output,
)

ENGINE = ScoreEngine(ScoringSettings())


class SleepRecorder:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def make_service(provider: FakeLLMProvider, **settings: object) -> tuple[LeadAnalysisService, SleepRecorder]:
    sleep = SleepRecorder()
    return LeadAnalysisService(provider, LLMSettings(provider="fake", **settings), sleep=sleep), sleep


def analyze_and_score(candidate, output):
    service, _ = make_service(FakeLLMProvider([output]))
    result = service.analyze(candidate)
    merged = merge_source_facts(candidate, result.analysis)
    return result, ENGINE.score(merged)


# --- Fachliche Szenarien ----------------------------------------------------


def test_small_clear_software_job():
    result, score = analyze_and_score(SMALL_CLEAR, SMALL_CLEAR_OUTPUT)

    assert result.attempts == 1 and result.grounding_issues == []
    assert result.analysis.scope_size == ScopeSize.SMALL
    assert result.analysis.budget_min == 4000 and result.analysis.estimated_person_days == 5
    assert score.total == 93 and score.lead_class == LeadClass.A


def test_consulting_job_for_sme():
    result, score = analyze_and_score(CONSULTING_SME, CONSULTING_SME_OUTPUT)

    assert result.analysis.consulting_fit == Level.HIGH
    assert result.analysis.customer_type == CustomerType.DIRECT_SME
    assert result.analysis.budget_min is None  # "Budget nach Absprache" -> unbekannt
    assert not score.hard_fail
    assert score.lead_class == LeadClass.B


def test_job_with_mandatory_certification():
    result, score = analyze_and_score(CERTIFICATION_REQUIRED, CERTIFICATION_REQUIRED_OUTPUT)

    assert result.grounding_issues == []  # Zertifikat, Ü2, 95 €/h und 6 Monate sind belegt
    assert result.analysis.required_certifications == ["ISTQB Advanced Level Test Manager"]
    assert {r.code for r in score.hard_fail_reasons} == {"missing_certification", "security_clearance"}
    assert score.lead_class == LeadClass.REJECT


def test_very_large_unsuitable_job():
    result, score = analyze_and_score(VERY_LARGE, VERY_LARGE_OUTPUT)

    assert result.analysis.estimated_person_days == 240  # "12 Monate Vollzeit" ist belegt
    assert {r.code for r in score.hard_fail_reasons} == {"staff_leasing", "scope_too_large"}
    assert score.lead_class == LeadClass.REJECT


def test_incomplete_posting_keeps_unknowns_null():
    result, score = analyze_and_score(INCOMPLETE, INCOMPLETE_HONEST_OUTPUT)

    analysis = result.analysis
    assert analysis.budget_min is None and analysis.estimated_person_days is None
    assert analysis.customer_type is None and analysis.required_certifications == []
    assert result.grounding_issues == []
    assert not score.hard_fail and score.lead_class == LeadClass.REJECT


def test_incomplete_posting_invented_facts_are_discarded():
    result, score = analyze_and_score(INCOMPLETE, INCOMPLETE_HALLUCINATED_OUTPUT)

    analysis = result.analysis
    assert analysis.budget_min is None and analysis.budget_max is None and analysis.currency is None
    assert analysis.required_certifications == [] and analysis.requires_security_clearance is None
    assert analysis.estimated_person_days is None
    assert len(result.grounding_issues) == 5
    assert not score.hard_fail  # erfundene Zertifizierung führt nicht zum Hard Fail


# --- Prompt-Injection -------------------------------------------------------


def test_prompt_injection_does_not_change_system_prompt_or_result():
    provider = FakeLLMProvider([INJECTION_CORRECT_OUTPUT])
    service, _ = make_service(provider)
    baseline_system = make_service(FakeLLMProvider())[0]._system_prompt

    result = service.analyze(INJECTION)

    request = provider.requests[0]
    assert request.system == baseline_system
    assert "IGNORE ALL PREVIOUS INSTRUCTIONS" in request.user  # nur als Daten
    assert request.user.count("</auftragstext>") == 1 and "<system>" not in request.user
    assert "Auftragstext enthält Anweisungen an KI-Systeme" in result.analysis.risks
    assert ENGINE.score(result.analysis).total < 100


def test_injected_score_output_is_rejected_and_retried():
    provider = FakeLLMProvider([INJECTION_FOOLED_OUTPUT, INJECTION_CORRECT_OUTPUT])
    service, _ = make_service(provider)

    result = service.analyze(INJECTION)

    assert result.attempts == 2
    assert "score_total" not in result.analysis.model_dump()
    retry_request = provider.requests[1].user
    assert "unzulässige Entscheidungsfelder" in retry_request
    assert retry_request.count("IGNORE ALL PREVIOUS INSTRUCTIONS") == 1  # kein Echo der Antwort


def test_persistently_injected_model_fails():
    service, _ = make_service(FakeLLMProvider(default=INJECTION_FOOLED_OUTPUT))

    with pytest.raises(AnalysisFailedError) as exc:
        service.analyze(INJECTION)

    assert exc.value.reason == "forbidden_fields" and exc.value.attempts == 3


# --- Strukturfehler und Retry -----------------------------------------------


def test_invalid_json_is_retried():
    provider = FakeLLMProvider(["Gerne, hier die Analyse: {summary: kaputt", SMALL_CLEAR_OUTPUT])
    service, sleep = make_service(provider)

    result = service.analyze(SMALL_CLEAR)

    assert result.attempts == 2 and provider.calls == 2
    assert "technisch ungültig (kein gültiges JSON" in provider.requests[1].user
    assert sleep.calls == []  # Strukturfehler: sofortige Wiederholung


def test_schema_error_is_retried_then_fails():
    bad = llm_output(estimated_person_days="fünf Tage")
    service, _ = make_service(FakeLLMProvider(default=bad), max_attempts=2)

    with pytest.raises(AnalysisFailedError) as exc:
        service.analyze(SMALL_CLEAR)

    assert exc.value.reason == "schema" and exc.value.attempts == 2
    assert all("estimated_person_days" in e for e in exc.value.errors)


def test_provider_timeout_is_retried_with_backoff():
    provider = FakeLLMProvider([LLMTimeoutError("read timeout"), SMALL_CLEAR_OUTPUT])
    service, sleep = make_service(provider, retry_backoff_seconds=1.5)

    result = service.analyze(SMALL_CLEAR)

    assert result.attempts == 2 and sleep.calls == [1.5]


def test_persistent_timeout_fails_after_max_attempts():
    provider = FakeLLMProvider(default=LLMTimeoutError("read timeout"))
    service, sleep = make_service(provider, max_attempts=3, retry_backoff_seconds=1)

    with pytest.raises(AnalysisFailedError) as exc:
        service.analyze(SMALL_CLEAR)

    assert exc.value.reason == "timeout" and exc.value.attempts == 3 and provider.calls == 3
    assert sleep.calls == [1, 2]


@pytest.mark.parametrize("error", [LLMRateLimitError("429"), LLMServerError("503"), LLMTruncatedOutputError("max_tokens")])
def test_transient_errors_are_retried(error):
    provider = FakeLLMProvider([error, SMALL_CLEAR_OUTPUT])

    assert make_service(provider)[0].analyze(SMALL_CLEAR).attempts == 2


@pytest.mark.parametrize("error", [LLMProviderError("HTTP 401: invalid x-api-key"), LLMRefusalError("refusal")])
def test_non_retryable_errors_fail_immediately(error):
    provider = FakeLLMProvider([error, SMALL_CLEAR_OUTPUT])
    service, sleep = make_service(provider)

    with pytest.raises(AnalysisFailedError) as exc:
        service.analyze(SMALL_CLEAR)

    assert provider.calls == 1 and sleep.calls == [] and exc.value.reason == error.reason


def test_valid_but_unfavorable_analysis_is_not_retried():
    """Kein Retry, um eine fachlich "bessere" Bewertung zu erzwingen."""
    unfavorable = llm_output(summary="Passt nicht.", technical_fit="low", entry_barrier="high", urgency="low")
    provider = FakeLLMProvider([unfavorable, SMALL_CLEAR_OUTPUT])

    result = make_service(provider)[0].analyze(SMALL_CLEAR)

    assert provider.calls == 1 and result.attempts == 1
    assert result.analysis.technical_fit == Level.LOW


def test_max_attempts_one_disables_retry():
    provider = FakeLLMProvider(["kaputt", SMALL_CLEAR_OUTPUT])

    with pytest.raises(AnalysisFailedError):
        make_service(provider, max_attempts=1)[0].analyze(SMALL_CLEAR)
    assert provider.calls == 1


# --- Usage, Kosten, Nachvollziehbarkeit --------------------------------------


def test_usage_and_cost_are_aggregated_over_attempts():
    usage = LLMUsage(input_tokens=1000, output_tokens=500)
    provider = FakeLLMProvider(["kaputt", SMALL_CLEAR_OUTPUT], usage=usage)
    service, _ = make_service(provider, pricing=LLMPricing(input_per_mtok=4, output_per_mtok=20))

    result = service.analyze(SMALL_CLEAR)

    assert result.usage.input_tokens == 2000 and result.usage.output_tokens == 1000
    assert result.usage.cost_usd == pytest.approx(0.028)


def test_missing_usage_information_is_tolerated():
    result = make_service(FakeLLMProvider([SMALL_CLEAR_OUTPUT], usage=None))[0].analyze(SMALL_CLEAR)

    assert result.usage is None


def test_usage_without_pricing_has_no_cost():
    provider = FakeLLMProvider([SMALL_CLEAR_OUTPUT], usage=LLMUsage(input_tokens=10, output_tokens=5))

    result = make_service(provider, pricing=None)[0].analyze(SMALL_CLEAR)

    assert result.usage.input_tokens == 10 and result.usage.cost_usd is None


def test_logs_contain_metadata_but_no_content(caplog):
    caplog.set_level("INFO", logger="src.llm.service")

    make_service(FakeLLMProvider([SMALL_CLEAR_OUTPUT]))[0].analyze(SMALL_CLEAR)

    record = next(r for r in caplog.records if r.getMessage() == "llm_call")
    assert record.outcome == "ok" and record.attempt == 1 and record.prompt_version.startswith("lead-analysis-v1+")
    assert all("PayPal" not in str(value) for value in record.__dict__.values())


def test_prompt_version_and_model_are_persisted_with_lead(tmp_path):
    provider = FakeLLMProvider([SMALL_CLEAR_OUTPUT], model="claude-opus-5-5")
    result = make_service(provider)[0].analyze(SMALL_CLEAR)
    merged = merge_source_facts(SMALL_CLEAR, result.analysis)
    lead = Lead.build(SMALL_CLEAR, merged, ENGINE.score(merged), prompt_version=result.prompt_version, llm_model=result.model)

    engine = create_db_engine(DatabaseSettings(url=f"sqlite:///{(tmp_path / 'db.sqlite').as_posix()}"))
    init_db(engine)
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        lead_id = LeadRepository(session).add(lead).id
    with session_scope(factory) as session:
        loaded = LeadRepository(session).get(lead_id)
    engine.dispose()

    assert loaded.llm_model == "claude-opus-5-5"
    assert loaded.prompt_version == result.prompt_version
    assert loaded.score_total == 93


# --- Offline-Fake-Provider --------------------------------------------------


def test_fake_provider_from_config_works_offline_without_api_key():
    provider = create_provider(LLMSettings(provider="fake"))
    service = LeadAnalysisService(provider, LLMSettings(provider="fake"))

    result = service.analyze(SMALL_CLEAR)

    assert result.provider == "fake" and result.model == "fake-model"
    assert result.analysis == result.analysis.model_validate({})  # alles unbekannt


def test_network_is_blocked_in_tests():
    with pytest.raises(RuntimeError, match="Netzwerkzugriff"):
        socket.create_connection(("api.anthropic.com", 443))


def test_fake_provider_scripted_callable_reply():
    provider = FakeLLMProvider([lambda request: {"summary": f"{len(request.user)} Zeichen"}])

    result = make_service(provider)[0].analyze(SMALL_CLEAR)

    assert result.analysis.summary.endswith("Zeichen")
    assert json.loads(json.dumps(provider.requests[0].json_schema))["additionalProperties"] is False
