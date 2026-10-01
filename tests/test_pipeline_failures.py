"""Fehlerfälle der Pipeline: Quellen-, LLM- und Datenbankfehler, LLM-Limit, Acknowledge-Verhalten."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from src.app import create_connectors
from src.domain.enums import CrawlRunStatus, ProcessingStatus
from src.llm import FakeLLMProvider
from src.llm.provider import LLMProviderError, LLMRequest, LLMTimeoutError
from src.sources.base import FetchResult, SourceConnector
from src.storage.database import session_scope
from src.storage.orm import ProcessedEmailRow
from src.storage.repository import CrawlRunRepository, LeadRepository
from tests.pipeline_support import EXPECTED, demo_provider, make_env

MULTI_KEY = "mid:20260930070211.4f2a1c@mailer.freelancermap.de"
CSV_ID = "2982210"


class BrokenConnector(SourceConnector):
    name = "portal-x"

    def fetch(self) -> FetchResult:
        raise ConnectionError("Portal nicht erreichbar")


def acknowledged_keys(env) -> set[str]:
    with session_scope(env.factory) as session:
        return set(session.scalars(select(ProcessedEmailRow.message_key)))


def failing_for(marker: str, error: Exception) -> FakeLLMProvider:
    """Demo-Antworten, aber Fehler für Kandidaten, deren Text ``marker`` enthält."""
    demo = demo_provider()

    def reply(request: LLMRequest):
        if marker in request.user:
            return error
        return demo.complete(request).text

    return FakeLLMProvider(default=reply)


# --- Quellenfehler ----------------------------------------------------------


def test_connector_error_does_not_stop_other_sources(tmp_path):
    env = make_env(tmp_path)
    connectors = [BrokenConnector(), *create_connectors(env.settings, env.factory, env.clock)]

    summary = env.pipeline(demo_provider(), connectors=connectors).run()

    assert summary.status == CrawlRunStatus.PARTIAL
    assert summary.errors == ["Quelle portal-x: ConnectionError: Portal nicht erreichbar"]
    assert len(env.leads()) == 6  # E-Mail-Quelle vollständig verarbeitet
    with session_scope(env.factory) as session:
        run = CrawlRunRepository(session).get(summary.run_id)
    assert run.status == CrawlRunStatus.PARTIAL and "portal-x" in run.error_message


def test_missing_mailbox_is_reported_as_source_error(tmp_path):
    env = make_env(tmp_path)
    settings = env.settings.model_copy(deep=True)
    settings.sources.email.mailbox_dir = tmp_path / "fehlt"

    summary = env.pipeline(demo_provider(), connectors=create_connectors(settings, env.factory)).run()

    assert summary.status == CrawlRunStatus.FAILED
    assert "Mail-Verzeichnis nicht gefunden" in summary.errors[0]


def test_unknown_source_name_is_rejected(tmp_path):
    env = make_env(tmp_path)
    with pytest.raises(ValueError, match="Unbekannte Quelle 'xing'"):
        env.pipeline(demo_provider()).run(source="xing")


# --- LLM-Fehler -------------------------------------------------------------


def test_llm_error_keeps_candidate_and_other_leads(tmp_path):
    env = make_env(tmp_path)
    provider = failing_for("CSV-Import", LLMTimeoutError("read timeout"))

    summary = env.pipeline(provider).run()

    leads = env.leads()
    csv = leads[CSV_ID]
    assert csv.processing_status == ProcessingStatus.PENDING_ANALYSIS
    assert csv.lead_class is None and csv.analysis_failures == 1 and "timeout" in csv.processing_error
    assert {sid for sid, lead in leads.items() if lead.processing_status == ProcessingStatus.ANALYZED} == {
        "2981734", "1187245", "1188003", "2979988"
    }
    assert summary.stats["llm_failures"] == 1 and summary.stats["pending_total"] == 1
    assert MULTI_KEY in acknowledged_keys(env)  # gespeichert (als pending) -> Mail bestätigt
    assert any("LLM-Analyse fehlgeschlagen" in w for w in summary.warnings)


def test_pending_after_llm_error_is_analyzed_in_next_run(tmp_path):
    env = make_env(tmp_path)
    env.pipeline(failing_for("CSV-Import", LLMTimeoutError("timeout"))).run()
    provider = demo_provider()

    second = env.pipeline(provider).run()

    csv = env.leads()[CSV_ID]
    assert (csv.lead_class.value, csv.score_total, csv.processing_status) == ("B", 79, ProcessingStatus.ANALYZED)
    assert csv.processing_error is None and csv.processed_run_id == second.run_id
    assert provider.calls == 1 and second.stats["pending_resumed"] == 1


def test_permanent_llm_failure_ends_as_analysis_failed(tmp_path):
    env = make_env(tmp_path, max_analysis_failures=2)
    provider = failing_for("CSV-Import", LLMProviderError("HTTP 400"))

    env.pipeline(provider).run()
    env.pipeline(provider).run()
    third = env.pipeline(provider).run()

    csv = env.leads()[CSV_ID]
    assert csv.processing_status == ProcessingStatus.ANALYSIS_FAILED and csv.analysis_failures == 2
    assert third.stats.get("pending_resumed", 0) == 0  # wird nicht endlos wiederholt


def test_unexpected_analyzer_exception_does_not_lose_candidate(tmp_path):
    env = make_env(tmp_path)

    def reply(request: LLMRequest):
        raise RuntimeError("Bug im Analyzer")

    env.pipeline(FakeLLMProvider(default=reply)).run()

    statuses = {lead.processing_status for lead in env.leads().values()}
    assert statuses == {ProcessingStatus.PENDING_ANALYSIS, ProcessingStatus.PREFILTERED}


# --- LLM-Limit --------------------------------------------------------------


def test_llm_limit_defers_candidates_without_losing_them(tmp_path):
    env = make_env(tmp_path, max_llm_analyses_per_run=2)
    provider = demo_provider()

    first = env.pipeline(provider).run()

    assert provider.calls == 2
    assert (first.stats["llm_analyses"], first.stats["deferred"], first.stats["pending_total"]) == (2, 3, 3)
    pending = [l for l in env.leads().values() if l.processing_status == ProcessingStatus.PENDING_ANALYSIS]
    assert len(pending) == 3 and all(l.processing_error == "LLM-Limit des Laufs erreicht" for l in pending)
    assert len(acknowledged_keys(env)) == 5  # alles gespeichert -> alle Mails bestätigt

    second = env.pipeline(provider).run()
    third = env.pipeline(provider).run()

    assert (second.stats["pending_resumed"], third.stats["pending_resumed"]) == (2, 1)
    assert provider.calls == 5
    leads = env.leads()
    assert {sid: (l.lead_class.value, l.score_total, l.processing_status.value) for sid, l in leads.items()} == EXPECTED


def test_llm_limit_zero_stores_everything_as_pending(tmp_path):
    env = make_env(tmp_path, max_llm_analyses_per_run=0)
    provider = demo_provider()

    summary = env.pipeline(provider).run()

    assert provider.calls == 0 and summary.stats["pending_total"] == 5 and summary.stats["prefiltered"] == 1


# --- Datenbankfehler und Acknowledge ----------------------------------------


def test_persistence_failure_prevents_acknowledge_and_is_retried(tmp_path, monkeypatch):
    env = make_env(tmp_path)
    original_add = LeadRepository.add

    def flaky_add(self, lead):
        if lead.source_id == CSV_ID:
            raise OperationalError("INSERT INTO leads", {}, Exception("database is locked"))
        return original_add(self, lead)

    monkeypatch.setattr(LeadRepository, "add", flaky_add)
    provider = demo_provider()
    first = env.pipeline(provider).run()

    assert first.status == CrawlRunStatus.PARTIAL and first.stats["persistence_errors"] == 1
    assert CSV_ID not in env.leads() and len(env.leads()) == 5  # andere Kandidaten gespeichert
    assert MULTI_KEY not in acknowledged_keys(env)  # Mail mit fehlgeschlagenem Lead NICHT bestätigt
    assert len(acknowledged_keys(env)) == 4
    assert first.stats["messages_not_acknowledged"] == 1

    monkeypatch.setattr(LeadRepository, "add", original_add)
    calls_before = provider.calls
    second = env.pipeline(provider).run()

    assert second.status == CrawlRunStatus.SUCCESS
    assert env.leads()[CSV_ID].score_total == 79
    assert provider.calls - calls_before == 1  # nur der fehlende Lead wird erneut analysiert
    assert second.stats["duplicates"] == 2  # Shopware und SAP aus der erneut gelesenen Mail
    assert MULTI_KEY in acknowledged_keys(env)


def test_acknowledge_failure_is_only_a_warning(tmp_path, monkeypatch):
    env = make_env(tmp_path)
    connectors = create_connectors(env.settings, env.factory, env.clock)

    def broken_ack(result):
        raise OperationalError("INSERT", {}, Exception("disk full"))

    monkeypatch.setattr(connectors[0], "acknowledge", broken_ack)
    summary = env.pipeline(demo_provider(), connectors=connectors).run()

    assert summary.status == CrawlRunStatus.SUCCESS and len(env.leads()) == 6
    assert any("Bestätigung fehlgeschlagen" in w for w in summary.warnings)
    second = env.pipeline(demo_provider()).run()  # Mails erneut gelesen, aber keine Doppelanalyse
    assert second.stats.get("llm_analyses", 0) == 0 and second.stats["duplicates"] == 7  # 7 Projekte, alle bekannt


def test_no_ack_mode_rereads_mails_without_reanalysis(tmp_path):
    env = make_env(tmp_path)
    provider = demo_provider()

    env.pipeline(provider, acknowledge=False).run()
    second = env.pipeline(provider, acknowledge=False).run()

    assert acknowledged_keys(env) == set()
    assert provider.calls == 5 and second.stats["duplicates"] == 7


def test_dedup_by_url_across_source_ids(tmp_path):
    """Gleiche Projekt-URL unter anderer ID (z. B. geänderte ID-Regel) wird nicht erneut analysiert."""
    from src.domain.models import RawSourceItem
    from tests.pipeline_support import StepClock

    env = make_env(tmp_path)
    env.pipeline(demo_provider()).run()
    item = RawSourceItem(
        source="freelancermap",
        source_item_id="andere-id",
        source_url="https://www.freelancermap.de/projekt/shopware-6-checkout-bugfix-2981734",
        received_at=StepClock()(),
        title="Shopware 6: Fehler im Checkout beheben",
        body_text="Text",
    )

    class OneItem(SourceConnector):
        name = "one"

        def fetch(self) -> FetchResult:
            return FetchResult(connector=self.name, items=[item])

    provider = demo_provider()
    summary = env.pipeline(provider, connectors=[OneItem()]).run()

    assert provider.calls == 0 and summary.stats["duplicates"] == 1
    with session_scope(env.factory) as session:
        lead = LeadRepository(session).find_by_url(item.source_url)
        sources = LeadRepository(session).list_sources(lead.id)
    assert ("freelancermap", "andere-id") in {(s.source, s.source_item_id) for s in sources}
