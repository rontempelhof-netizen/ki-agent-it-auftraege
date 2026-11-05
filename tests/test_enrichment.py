"""Tests der kostenkontrollierten Recherche (sämtliche HTTP-Antworten lokal simuliert)."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from src.app import build_pipeline
from src.config import EnrichmentSettings
from src.domain.enums import ProcessingStatus, LeadClass, Level
from src.domain.models import Lead, LeadAnalysis
from src.main import main
from src.llm import FakeLLMProvider
from src.pipeline.enrichment import BraveContextEnricher, EnrichmentMatch, _match_strength
from src.storage.database import session_scope
from src.storage.repository import LeadRepository
from tests.fixtures.leads import make_candidate
from tests.pipeline_support import make_env


class Response:
    def __init__(self, data: dict) -> None:
        self._data = json.dumps(data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _max_bytes: int) -> bytes:
        return self._data


def _candidate():
    return make_candidate(
        source="freelance.de",
        source_id="1300147",
        title="Python Developer Datenmigration PyCharm Oracle GitLab",
        description="Python, Oracle, GitLab. Alle weiteren Angaben fehlen.",
    )


def test_disabled_enrichment_needs_no_key(monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    from src.pipeline.enrichment import build_enricher

    assert build_enricher(EnrichmentSettings()) is None
    with pytest.raises(ValueError, match="BRAVE_API_KEY"):
        build_enricher(EnrichmentSettings(enabled=True))


def test_brave_context_fetches_only_fixed_api_and_matches_source_id(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "test-secret-not-logged")
    called = []

    def urlopen(request, timeout):
        called.append((request, timeout))
        return Response({
            "grounding": {"generic": [{
                "url": "https://www.nemensis.de/projekt/1300147-python-migration",
                "title": "Python Developer Datenmigration",
                "snippets": [
                    "Für die Datenmigration werden Python, Oracle, GitLab, PyCharm genutzt.",
                    "Auslastung 100 Prozent, Start 12/2026, Details nach Rücksprache.",
                ],
            }]}
        })

    monkeypatch.setattr("src.pipeline.enrichment.urlopen", urlopen)
    enricher = BraveContextEnricher(EnrichmentSettings(enabled=True))
    result = enricher.enrich(_candidate())

    assert result is not None and "Auslastung" in result.text
    assert result.url.startswith("https://www.nemensis.de")
    assert len(called) == 1
    request, timeout = called[0]
    assert urlsplit(request.full_url).hostname == "api.search.brave.com"
    assert request.get_header("X-subscription-token") == "test-secret-not-logged"
    query = parse_qs(urlsplit(request.full_url).query)["q"][0]
    assert "python" in query and "pycharm" in query
    assert parse_qs(urlsplit(request.full_url).query)["country"] == ["DE"]
    assert parse_qs(urlsplit(request.full_url).query)["search_lang"] == ["de"]


def test_ambiguous_titles_and_unrelated_results_are_rejected(monkeypatch):
    candidate = _candidate()
    score, reason = _match_strength(
        candidate, "Senior Java Entwickler für die Automobilindustrie",
        "Eine andere Stelle, keine Datenmigration oder Pythonkenntnisse.",
        "https://example.com/job",
    )
    assert score == 0
    assert _match_strength(candidate, "Python Developer Datenmigration PyCharm Oracle GitLab", "xyz", "http://example.com")[0] == 0
    monkeypatch.setenv("BRAVE_API_KEY", "fake")

    def urlopen(_request, timeout):
        return Response({
            "grounding": {"generic": [
                {"title": candidate.title, "url": "https://one.example/project",
                 "snippets": ["Projekt mit Python, Oracle, Gitlab und Datenmigration " * 3]},
                {"title": candidate.title, "url": "https://two.example/project",
                 "snippets": ["Projekt mit Python, Oracle, Gitlab und Datenmigration " * 3]},
            ]}
        })
    monkeypatch.setattr("src.pipeline.enrichment.urlopen", urlopen)
    assert BraveContextEnricher(EnrichmentSettings(enabled=True)).enrich(candidate) is None


class StubEnricher:
    def __init__(self):
        self.calls = 0

    def should_search(self, candidate):
        return True

    def enrich(self, candidate):
        self.calls += 1
        return EnrichmentMatch(
            url="https://example.com/auftrag",
            title="Python data migration",
            text="Anforderungen: Python und Oracle. Festpreis 4000 EUR.",
            evidence="Testfixture",
        )


def test_pipeline_enriches_only_llm_request_and_preserves_email_source(tmp_path):
    env = make_env(tmp_path, max_llm_analyses_per_run=0)
    env.pipeline(FakeLLMProvider()).run()
    with session_scope(env.factory) as session:
        pending = LeadRepository(session).list_pending()
    selected = pending[0]
    original = selected.description
    stub = StubEnricher()
    provider = FakeLLMProvider()
    pipeline = env.pipeline(provider, connectors=[])
    pipeline._enricher = stub
    pipeline._max_enrichment_requests = 1
    pipeline._settings = pipeline._settings.model_copy(update={"max_llm_analyses_per_run": 1})

    result = pipeline.run_selected(selected.id)

    assert result.stats["enrichment_queries"] == 1
    assert result.stats["enrichment_matches"] == 1
    assert stub.calls == 1
    assert "Zusätzlicher externer Recherchekontext" in provider.requests[0].user
    assert "Festpreis 4000 EUR" in provider.requests[0].user
    with session_scope(env.factory) as session:
        stored = LeadRepository(session).get(selected.id)
    assert stored.processing_status == ProcessingStatus.ANALYZED
    assert stored.description == original  # E-Mail-Original nicht mit Suchauszügen überschrieben


def test_search_budget_is_independent_and_limits_requests(tmp_path):
    env = make_env(tmp_path, max_llm_analyses_per_run=0)
    env.pipeline(FakeLLMProvider()).run()
    stub = StubEnricher()
    provider = FakeLLMProvider()
    pipeline = env.pipeline(provider, connectors=[])
    pipeline._enricher = stub
    pipeline._max_enrichment_requests = 1
    pipeline._settings = pipeline._settings.model_copy(update={"max_llm_analyses_per_run": 3})

    summary = pipeline.run()

    assert summary.stats["llm_analyses"] == 3
    assert summary.stats["enrichment_queries"] == 1
    assert stub.calls == 1
    assert provider.calls == 3



def test_enrich_preview_works_on_existing_lead_without_ai_or_db_writes(tmp_path, monkeypatch, capsys):
    env = make_env(tmp_path, max_llm_analyses_per_run=0)
    with session_scope(env.factory) as session:
        already_analyzed = Lead.pending(_candidate()).model_copy(update={
            "processing_status": ProcessingStatus.ANALYZED,
            "lead_class": LeadClass.REJECT,
            "analysis": LeadAnalysis(technical_fit=Level.HIGH),
        })
        saved = LeadRepository(session).add(already_analyzed)
    monkeypatch.setenv("AGENT_DATABASE__URL", env.settings.database.url)
    monkeypatch.setenv("BRAVE_API_KEY", "secret-for-test")

    def urlopen(_request, timeout):
        return Response({"grounding": {"generic": [{
            "url": "https://nemensis.example/auftrag-1300147",
            "title": "Python Developer Datenmigration Oracle",
            "snippets": ["Projekt mit PyCharm, Python und SQL/Oracle, GitLab " * 4],
        }]}})
    monkeypatch.setattr("src.pipeline.enrichment.urlopen", urlopen)

    assert main(["enrich-preview", "--id", str(saved.id)]) == 0
    output = capsys.readouterr().out
    assert "nemensis.example" in output
    assert "kein Claude" in output
    assert "secret-for-test" not in output
    with session_scope(env.factory) as session:
        assert LeadRepository(session).get(saved.id) == saved
    assert main(["enrich-preview", "--id", "9999"]) == 2



def test_context_diagnostics_distinguish_no_results_and_strict_rejection(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "fake")
    responses = [
        {"grounding": {"generic": []}},
        {"grounding": {"generic": [{
            "url": "https://example.com/andere-stelle",
            "title": "Senior Java Entwickler",
            "snippets": ["Gesucht wird Java Developer für eine andere Position. " * 4],
        }]}},
    ]

    def urlopen(_request, timeout):
        return Response(responses.pop(0))

    monkeypatch.setattr("src.pipeline.enrichment.urlopen", urlopen)
    enricher = BraveContextEnricher(EnrichmentSettings(enabled=True))

    assert enricher.enrich(_candidate()) is None
    assert enricher.last_diagnostics is not None
    assert enricher.last_diagnostics.total_results == 0
    assert enricher.last_diagnostics.outcome == "keine Suchtreffer"

    assert enricher.enrich(_candidate()) is None
    assert enricher.last_diagnostics is not None
    assert enricher.last_diagnostics.total_results == 1
    assert enricher.last_diagnostics.outcome == "keine hinreichend passenden Treffer"
    assert enricher.last_diagnostics.candidates[0].score == 0
    assert "Abgleich" in enricher.last_diagnostics.candidates[0].reason


def test_title_matching_accepts_strong_description_evidence_with_shortened_original_title():
    candidate = _candidate()
    score, why = _match_strength(
        candidate,
        "Python Developer Datenmigration",
        "Datenmigration mit Python, Oracle, PyCharm und GitLab. Umfassende Details.",
        "https://origin.example.com/python-entwicklung",
    )
    assert score == 1.0
    assert "Titelabgleich" in why


def test_cli_diagnose_lists_brave_rejection_reasons_without_extra_requests(tmp_path, monkeypatch, capsys):
    env = make_env(tmp_path, max_llm_analyses_per_run=0)
    with session_scope(env.factory) as session:
        saved = LeadRepository(session).add(Lead.pending(_candidate()))

    monkeypatch.setenv("AGENT_DATABASE__URL", env.settings.database.url)
    monkeypatch.setenv("BRAVE_API_KEY", "redacted-key")
    calls = []

    def urlopen(request, timeout):
        calls.append(request)
        return Response({"grounding": {"generic": [{
            "url": "https://example.org/stellenangebot",
            "title": "Java Backend Entwickler",
            "snippets": ["Eine völlig andere Java-Stelle mit anderem technischen Schwerpunkt. " * 3],
        }]}})

    monkeypatch.setattr("src.pipeline.enrichment.urlopen", urlopen)
    assert main(["enrich-preview", "--id", str(saved.id), "--diagnose"]) == 0
    out = capsys.readouterr().out
    assert "Diagnose: keine hinreichend passenden Treffer" in out
    assert "Treffer 1:" in out
    assert "Suchanfrage:" in out
    assert "Prüfung:" in out
    assert "redacted-key" not in out
    assert len(calls) == 1
