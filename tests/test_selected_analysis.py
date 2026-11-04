"""Sichere Einzelauswahl für fachliche Kalibrierung: keine ungewollten API-Aufrufe."""

from __future__ import annotations

import pytest

from src.domain.enums import ProcessingStatus
from src.llm import FakeLLMProvider
from src.main import main
from src.storage.database import session_scope
from src.storage.repository import CrawlRunRepository, LeadRepository
from tests.pipeline_support import make_env


def seed_pending(tmp_path):
    env = make_env(tmp_path, max_llm_analyses_per_run=0)
    first = env.pipeline(FakeLLMProvider()).run()
    assert first.stats["llm_analyses"] == 0
    with session_scope(env.factory) as session:
        pending = LeadRepository(session).list_pending()
    assert len(pending) == 5
    return env, pending


def select_pipeline(env, provider):
    env.settings = env.settings.model_copy(
        update={"pipeline": env.settings.pipeline.model_copy(update={"max_llm_analyses_per_run": 1})}
    )
    return env.pipeline(provider)


def test_selected_analysis_touches_only_explicit_id_no_email_fetch(tmp_path):
    env, pending = seed_pending(tmp_path)
    chosen = pending[2]
    assert chosen.id is not None
    provider = FakeLLMProvider()

    # env.pipeline normally includes an active email connector. run_selected must not fetch it.
    result = select_pipeline(env, provider).run_selected(chosen.id)

    assert provider.calls == 1
    assert chosen.title in provider.requests[0].user
    assert result.stats["llm_analyses"] == 1 and result.stats["pending_resumed"] == 1
    assert result.stats.get("mails_read", 0) == 0
    assert result.stats.get("candidates_new", 0) == 0
    assert result.lead_ids == [chosen.id]
    with session_scope(env.factory) as session:
        repo = LeadRepository(session)
        assert repo.get(chosen.id).processing_status == ProcessingStatus.ANALYZED
        remaining = repo.list_pending()
        assert len(remaining) == 4
        assert {l.id for l in remaining} == {l.id for l in pending if l.id != chosen.id}
        assert CrawlRunRepository(session).get(result.run_id).source == f"selected-lead:{chosen.id}"


def test_invalid_or_completed_selection_never_calls_provider_or_creates_run(tmp_path):
    env, pending = seed_pending(tmp_path)
    provider = FakeLLMProvider()
    pipeline = select_pipeline(env, provider)
    with session_scope(env.factory) as session:
        run_count = len(CrawlRunRepository(session).list_recent())

    for bad_id in (0, -1, 9999):
        with pytest.raises(ValueError):
            pipeline.run_selected(bad_id)
    assert pending[0].id is not None
    pipeline.run_selected(pending[0].id)
    assert provider.calls == 1
    with pytest.raises(ValueError, match="nicht ausstehend"):
        pipeline.run_selected(pending[0].id)
    with session_scope(env.factory) as session:
        assert len(CrawlRunRepository(session).list_recent()) == run_count + 1


def test_prefiltered_lead_cannot_be_explicitly_reanalyzed(tmp_path):
    env, _ = seed_pending(tmp_path)
    with session_scope(env.factory) as session:
        prefiltered = next(l for l in LeadRepository(session).list_leads()
                           if l.processing_status == ProcessingStatus.PREFILTERED)
    provider = FakeLLMProvider()
    with pytest.raises(ValueError, match="nicht ausstehend"):
        select_pipeline(env, provider).run_selected(prefiltered.id)
    assert provider.calls == 0


def test_pending_search_matches_title_and_description_case_insensitively(tmp_path):
    env, pending = seed_pending(tmp_path)
    with session_scope(env.factory) as session:
        repo = LeadRepository(session)
        shopware = repo.list_pending(search="SHOPWARE")
        assert len(shopware) == 1 and "Shopware" in shopware[0].title
        assert repo.list_pending(search="%") == []  # SQL-Wildcards literal
        assert repo.list_pending(search="_") == []  # SQL-Wildcards literal
        assert len(repo.list_pending(limit=2)) == 2
        assert [l.id for l in repo.list_pending()] == [l.id for l in pending]


def test_cli_lists_pending_and_fake_analyzes_one_without_gmail(tmp_path, monkeypatch, capsys):
    env, pending = seed_pending(tmp_path)
    monkeypatch.setenv("AGENT_DATABASE__URL", env.settings.database.url)
    monkeypatch.setenv("AGENT_SOURCES__EMAIL__BACKEND", "gmail")  # kein Gmail-OAuth im Test
    monkeypatch.setenv("AGENT_MAIL__MODE", "dry_run")
    assert main(["pending-leads", "--search", "Python", "--limit", "5"]) == 0
    assert "Lead #" in capsys.readouterr().out
    assert main(["pending-leads", "--limit", "0"]) == 2
    assert main(["analyze-lead", "--id", "9999", "--fake-llm"]) == 2
    assert main(["analyze-lead", "--id", str(pending[1].id), "--fake-llm"]) == 0
    out = capsys.readouterr().out
    assert "Keine E-Mails gelesen oder versendet" in out
    assert "LLM 1" in out
    with session_scope(env.factory) as session:
        assert len(LeadRepository(session).list_pending()) == 4
    assert main(["analyze-lead", "--id", str(pending[1].id), "--fake-llm"]) == 2
