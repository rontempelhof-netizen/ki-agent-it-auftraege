"""Zweite fachliche Sichtung ohne Änderung von Scores, Status oder der Datenbankstruktur."""

from __future__ import annotations

from datetime import UTC, datetime

from src.config import DatabaseSettings, ReportSettings, ScoringSettings
from src.domain.enums import LeadClass, Level, ProcessingStatus, RemoteStatus, ScopeClarity
from src.domain.models import CrawlRun, Lead, LeadAnalysis
from src.main import main
from src.report.builder import build_report
from src.report.render import render_html, render_text
from src.scoring.engine import ScoreEngine
from src.scoring.review_triage import review_advice
from src.storage.database import create_db_engine, create_session_factory, init_db, session_scope
from src.storage.repository import LeadRepository
from tests.fixtures.leads import make_candidate


def _lead(
    *,
    title: str = "Python Datenmigration 100% Remote",
    description: str = "Remote Python Entwickler für SQL-Datenmigration.",
    technical_fit: Level | None = Level.HIGH,
    consulting_fit: Level | None = Level.LOW,
    entry_barrier: Level | None = None,
    budget_min: float | None = None,
    customer_type=None,
    estimated_person_days: float | None = None,
    scope_size=None,
    required_certifications=None,
) -> Lead:
    analysis = LeadAnalysis(
        technical_fit=technical_fit,
        consulting_fit=consulting_fit,
        entry_barrier=entry_barrier,
        budget_min=budget_min,
        customer_type=customer_type,
        estimated_person_days=estimated_person_days,
        scope_size=scope_size,
        required_certifications=required_certifications or [],
        scope_clarity=ScopeClarity.VAGUE,
        urgency=Level.LOW,
        remote_status=RemoteStatus.REMOTE,
        fit_reason="Python und SQL passen gut, wirtschaftliche Daten fehlen.",
    )
    candidate = make_candidate(
        source="freelance.de", source_id="1300147",
        title=title, description=description, customer_name=None,
        budget_min=None, budget_max=None, currency=None, budget_type=None,
    )
    score = ScoreEngine(ScoringSettings()).score(analysis)
    return Lead.build(candidate, analysis, score)


def test_python_162_equivalent_is_flagged_but_stays_reject_with_44_points():
    lead = _lead()
    assert lead.score_total == 44
    assert lead.lead_class == LeadClass.REJECT
    advice = review_advice(lead)
    assert advice is not None
    assert "Budget oder Tagessatz fehlt" in advice.reasons
    assert "Auftragsumfang oder Dauer fehlt" in advice.reasons
    assert "Quellbeschreibung kurz – vollständige Ausschreibung prüfen" in advice.reasons
    assert lead.score_total == 44 and lead.lead_class == LeadClass.REJECT


def test_low_or_unknown_fit_never_qualifies():
    assert review_advice(_lead(technical_fit=Level.LOW)) is None
    assert review_advice(_lead(technical_fit=None)) is None


def test_explicit_high_consulting_fit_can_qualify():
    advice = review_advice(_lead(technical_fit=Level.MEDIUM, consulting_fit=Level.HIGH))
    assert advice is not None


def test_hard_fails_and_non_analyzed_leads_never_qualify():
    disqualified = _lead(required_certifications=["AWS Zertifizierung Pflicht"])
    assert disqualified.hard_fail
    assert review_advice(disqualified) is None

    ordinary = _lead()
    assert review_advice(ordinary.model_copy(update={"processing_status": ProcessingStatus.PREFILTERED})) is None
    pending = Lead.pending(make_candidate())
    assert pending.processing_status == ProcessingStatus.PENDING_ANALYSIS
    assert review_advice(pending) is None
    prefiltered = Lead.prefiltered(make_candidate(), ["Festanstellung"])
    assert review_advice(prefiltered) is None


def test_insufficient_information_gaps_do_not_trigger_review():
    from src.domain.enums import CustomerType, ScopeSize
    lead = _lead(
        entry_barrier=Level.MEDIUM,
        budget_min=5000,
        customer_type=CustomerType.DIRECT_SME,
        estimated_person_days=10,
        scope_size=ScopeSize.SMALL,
    )
    assert review_advice(lead) is None


def test_long_source_text_does_not_claim_it_is_short():
    lead = _lead(description="Python Data Engineering, SQL and migration. " * 12)
    advice = review_advice(lead)
    assert advice is not None
    assert not advice.short_source_text
    assert not any("kurz" in reason for reason in advice.reasons)


def test_review_report_keeps_reject_counts_and_separate_section():
    lead = _lead(title="<img src=x onerror=alert(1)> Python Migration")
    advice = review_advice(lead)
    assert advice is not None
    now = datetime(2026, 11, 5, 7, 0, tzinfo=UTC)
    report = build_report(
        CrawlRun(id=7, started_at=now, finished_at=now),
        [lead], ReportSettings(), now,
    )
    assert report.class_counts[LeadClass.REJECT.value] == 1
    assert report.leads == []
    assert len(report.review_leads) == 1
    assert report.review_leads[0].score == 44
    html = render_html(report)
    text = render_text(report)
    assert "Fachlich interessant – Angaben klären" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert '<img src=x onerror=alert(1)>' not in html
    assert "KLÄRFALL · REJECT 44" in text
    assert "Budget oder Tagessatz fehlt" in html and "Budget oder Tagessatz fehlt" in text


def test_review_leads_cli_reads_existing_analysis_without_changing_data(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "review.db"
    db_url = f"sqlite:///{db_path.as_posix()}"
    engine = create_db_engine(DatabaseSettings(url=db_url))
    init_db(engine)
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        saved = LeadRepository(session).add(_lead())
    engine.dispose()

    monkeypatch.setenv("AGENT_DATABASE__URL", db_url)
    monkeypatch.setenv("AGENT_SOURCES__EMAIL__BACKEND", "gmail")
    assert main(["review-leads", "--id", str(saved.id)]) == 0
    out = capsys.readouterr().out
    assert f"Lead #{saved.id}" in out
    assert "REJECT 44/100" in out
    assert "Keine Neubewertung" in out
    assert main(["review-leads", "--limit", "0"]) == 2
    assert main(["review-leads", "--id", "999"]) == 2
    with session_scope(factory) as session:
        loaded = LeadRepository(session).get(saved.id)
        assert loaded == saved
