from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.config import DatabaseSettings, ScoringSettings
from src.domain.enums import CrawlRunStatus, LeadClass, LeadStatus
from src.domain.models import CrawlRun, Feedback, Lead, LeadAnalysis, LeadCandidate, merge_source_facts
from src.domain.status import InvalidStatusTransitionError
from src.scoring.engine import ScoreEngine
from src.storage.database import create_db_engine, create_session_factory, init_db, session_scope
from src.storage.orm import LeadScoreDetailRow
from src.storage.repository import (
    CrawlRunRepository,
    DuplicateLeadError,
    FeedbackRepository,
    LeadNotFoundError,
    LeadRepository,
)
from tests.fixtures.leads import (
    ALL_UNKNOWN,
    CSV_IMPORT_79,
    ERP_SHOP_API_VIA_AGENCY,
    LEGACY_PHP_50,
    SAP_STAFF_LEASING,
    SEEN_AT,
    SHOPWARE_BUGFIX,
    make_candidate,
)

ENGINE = ScoreEngine(ScoringSettings())


def build_lead(analysis: LeadAnalysis, candidate: LeadCandidate | None = None) -> Lead:
    candidate = candidate or make_candidate()
    return Lead.build(candidate, analysis, ENGINE.score(analysis), prompt_version="analysis-v1")


@pytest.fixture
def session_factory(tmp_path):
    engine = create_db_engine(DatabaseSettings(url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}"))
    init_db(engine)
    yield create_session_factory(engine)
    engine.dispose()


@pytest.fixture
def session(session_factory) -> Iterator[Session]:
    with session_scope(session_factory) as session:
        yield session


def test_save_and_load_complete_lead(session_factory):
    lead = build_lead(SHOPWARE_BUGFIX.analysis)

    with session_scope(session_factory) as session:
        lead_id = LeadRepository(session).add(lead).id

    with session_scope(session_factory) as session:  # neue Session -> wirklich aus SQLite geladen
        loaded = LeadRepository(session).get(lead_id)

    assert loaded is not None
    assert loaded.model_dump(exclude={"id"}) == lead.model_dump(exclude={"id"})
    assert loaded.analysis == merge_source_facts(make_candidate(), SHOPWARE_BUGFIX.analysis)
    assert loaded.first_seen_at == SEEN_AT and loaded.first_seen_at.tzinfo is not None


def test_save_and_load_lead_with_missing_optional_data(session_factory):
    candidate = make_candidate(
        source_id="fl-1", source_url=None, published_at=None, location=None,
        remote_status=None, language=None, customer_name=None,
    )
    lead = build_lead(ALL_UNKNOWN.analysis, candidate)

    with session_scope(session_factory) as session:
        lead_id = LeadRepository(session).add(lead).id
    with session_scope(session_factory) as session:
        loaded = LeadRepository(session).get(lead_id)

    assert loaded == lead.model_copy(update={"id": lead_id})
    assert loaded.budget_min is None and loaded.published_at is None and loaded.customer_type is None


def test_score_breakdown_persisted_per_criterion(session):
    saved = LeadRepository(session).add(build_lead(ERP_SHOP_API_VIA_AGENCY.analysis))

    rows = session.scalars(select(LeadScoreDetailRow).where(LeadScoreDetailRow.lead_id == saved.id)).all()

    assert len(rows) == 9
    assert sum(r.points for r in rows) == saved.score_total == ERP_SHOP_API_VIA_AGENCY.expected_total
    assert {r.criterion: r.points for r in rows} == ERP_SHOP_API_VIA_AGENCY.expected_points


def test_hard_fail_lead_persisted(session):
    saved = LeadRepository(session).add(build_lead(SAP_STAFF_LEASING.analysis))

    loaded = LeadRepository(session).get(saved.id)

    assert loaded.hard_fail and loaded.lead_class == LeadClass.REJECT
    assert len(loaded.hard_fail_reasons) == 3


def test_timezone_is_normalized_to_utc(session):
    cest = timezone(timedelta(hours=2))
    candidate = make_candidate(first_seen_at=datetime(2026, 9, 30, 9, 15, tzinfo=cest))

    saved = LeadRepository(session).add(build_lead(SHOPWARE_BUGFIX.analysis, candidate))

    assert LeadRepository(session).get(saved.id).first_seen_at == datetime(2026, 9, 30, 7, 15, tzinfo=UTC)


def test_duplicate_lead_rejected(session):
    repo = LeadRepository(session)
    repo.add(build_lead(SHOPWARE_BUGFIX.analysis))

    with pytest.raises(DuplicateLeadError):
        repo.add(build_lead(SHOPWARE_BUGFIX.analysis))


def test_get_by_source_ref_and_additional_occurrence(session):
    repo = LeadRepository(session)
    saved = repo.add(build_lead(SHOPWARE_BUGFIX.analysis))

    repo.record_source(saved.id, "freelance.de", "fd-777", seen_at=SEEN_AT + timedelta(hours=3))
    repo.record_source(saved.id, "freelance.de", "fd-777", seen_at=SEEN_AT + timedelta(days=1))

    assert repo.get_by_source_ref("freelancermap", "fm-2981734").id == saved.id
    assert repo.get_by_source_ref("freelance.de", "fd-777").id == saved.id
    assert repo.get_by_source_ref("freelance.de", "unknown") is None
    sources = repo.list_sources(saved.id)
    assert [(s.source, s.source_item_id) for s in sources] == [("freelancermap", "fm-2981734"), ("freelance.de", "fd-777")]
    assert sources[1].last_seen_at == SEEN_AT + timedelta(days=1)
    assert sources[1].first_seen_at == SEEN_AT + timedelta(hours=3)


def test_occurrence_of_other_lead_rejected(session):
    repo = LeadRepository(session)
    first = repo.add(build_lead(SHOPWARE_BUGFIX.analysis))
    second = repo.add(build_lead(CSV_IMPORT_79.analysis, make_candidate(source_id="fm-2")))

    with pytest.raises(DuplicateLeadError):
        repo.record_source(second.id, "freelancermap", "fm-2981734", seen_at=SEEN_AT)
    assert first.id != second.id


def test_list_leads_sorted_and_filtered(session):
    repo = LeadRepository(session)
    for i, case in enumerate([LEGACY_PHP_50, SHOPWARE_BUGFIX, ERP_SHOP_API_VIA_AGENCY, CSV_IMPORT_79, SAP_STAFF_LEASING]):
        repo.add(build_lead(case.analysis, make_candidate(source_id=f"fm-{i}")))

    all_scores = [lead.score_total for lead in repo.list_leads()]
    a_and_b = repo.list_leads(classes=[LeadClass.A, LeadClass.B])

    assert all_scores == sorted(all_scores, reverse=True)
    assert [(l.lead_class, l.score_total) for l in a_and_b] == [(LeadClass.A, 93), (LeadClass.B, 79), (LeadClass.B, 68)]
    assert len(repo.list_leads(limit=2)) == 2
    assert [l.score_total for l in repo.list_leads(min_score=79)] == [93, 79]
    assert len(repo.list_leads(statuses=[LeadStatus.REVIEWED])) == 0


def test_update_status_valid_and_invalid(session):
    repo = LeadRepository(session)
    lead_id = repo.add(build_lead(SHOPWARE_BUGFIX.analysis)).id

    assert repo.update_status(lead_id, LeadStatus.REVIEWED).status == LeadStatus.REVIEWED
    with pytest.raises(InvalidStatusTransitionError):
        repo.update_status(lead_id, LeadStatus.WON)
    assert repo.get(lead_id).status == LeadStatus.REVIEWED


def test_update_status_unknown_lead(session):
    with pytest.raises(LeadNotFoundError):
        LeadRepository(session).update_status(999, LeadStatus.REVIEWED)


def test_update_score_replaces_breakdown(session):
    repo = LeadRepository(session)
    lead_id = repo.add(build_lead(SHOPWARE_BUGFIX.analysis)).id
    new_analysis = SHOPWARE_BUGFIX.analysis.model_copy(update={"requires_security_clearance": True})

    updated = repo.update_score(lead_id, ENGINE.score(new_analysis), analysis=new_analysis, prompt_version="analysis-v2")

    assert updated.lead_class == LeadClass.REJECT and updated.hard_fail
    assert updated.prompt_version == "analysis-v2"
    assert updated.analysis.requires_security_clearance is True
    count = session.scalar(select(func.count()).select_from(LeadScoreDetailRow).where(LeadScoreDetailRow.lead_id == lead_id))
    assert count == 9


def test_crawl_run_lifecycle(session):
    repo = CrawlRunRepository(session)
    run = repo.add(CrawlRun(source="freelancermap", started_at=SEEN_AT))

    finished = repo.update(
        run.model_copy(
            update={
                "status": CrawlRunStatus.PARTIAL,
                "finished_at": SEEN_AT + timedelta(minutes=2),
                "items_found": 12, "items_new": 5, "items_analyzed": 5, "items_rejected": 2,
                "warnings": ["freelance.de: Timeout"],
            }
        )
    )

    assert finished.id == run.id
    assert repo.get(run.id) == finished
    assert finished.status == CrawlRunStatus.PARTIAL and finished.warnings == ["freelance.de: Timeout"]
    assert [r.id for r in repo.list_recent()] == [run.id]


def test_crawl_run_update_unknown(session):
    with pytest.raises(LookupError):
        CrawlRunRepository(session).update(CrawlRun(id=42, started_at=SEEN_AT))


def test_feedback_add_and_list(session):
    lead_id = LeadRepository(session).add(build_lead(SHOPWARE_BUGFIX.analysis)).id
    repo = FeedbackRepository(session)

    repo.add(Feedback(lead_id=lead_id, created_at=SEEN_AT, relevant=True, rating=5, comment="Passt genau"))
    repo.add(Feedback(lead_id=lead_id, created_at=SEEN_AT, comment="Kunde hat geantwortet"))

    items = repo.list_for_lead(lead_id)
    assert [f.comment for f in items] == ["Passt genau", "Kunde hat geantwortet"]
    assert items[0].created_at == SEEN_AT


def test_feedback_for_unknown_lead(session):
    with pytest.raises(LeadNotFoundError):
        FeedbackRepository(session).add(Feedback(lead_id=999, created_at=SEEN_AT, relevant=False))


def test_rollback_on_error(session_factory):
    with pytest.raises(RuntimeError), session_scope(session_factory) as session:
        LeadRepository(session).add(build_lead(SHOPWARE_BUGFIX.analysis))
        raise RuntimeError("Abbruch")

    with session_scope(session_factory) as session:
        assert LeadRepository(session).list_leads() == []
