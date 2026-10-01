"""Verdrahtung: erzeugt die konkreten Komponenten aus der Konfiguration.

Einziger Ort, an dem Connectoren, LLM-Provider, Prefilter, Score Engine, Persistenz,
Report und Mailer zusammengesetzt werden. Die Komponenten selbst kennen sich nicht.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from src.config import Settings
from src.llm import LeadAnalysisService, LLMProvider, create_provider
from src.notify.mailer import DeliveryResult, OutgoingMail, create_mailer
from src.pipeline.prefilter import Prefilter
from src.pipeline.runner import Pipeline
from src.report import DailyReport, build_report, render_html, render_text
from src.scoring.engine import ScoreEngine
from src.sources.base import SourceConnector
from src.sources.email import EmailSourceConnector, EmlDirectoryMailbox
from src.storage.database import session_scope
from src.storage.repository import CrawlRunRepository, LeadRepository, ScopedProcessedEmailStore

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


def create_connectors(settings: Settings, session_factory: sessionmaker[Session], clock: Clock = utc_now) -> list[SourceConnector]:
    connectors: list[SourceConnector] = []
    email = settings.sources.email
    if email.enabled:
        connectors.append(
            EmailSourceConnector(
                email,
                EmlDirectoryMailbox(email.mailbox_dir),
                ScopedProcessedEmailStore(session_factory),
                clock=clock,
            )
        )
    return connectors


def build_pipeline(
    settings: Settings,
    session_factory: sessionmaker[Session],
    *,
    provider: LLMProvider | None = None,
    connectors: list[SourceConnector] | None = None,
    acknowledge: bool = True,
    clock: Clock = utc_now,
) -> Pipeline:
    analyzer = LeadAnalysisService(provider or create_provider(settings.llm), settings.llm)
    return Pipeline(
        connectors=connectors if connectors is not None else create_connectors(settings, session_factory, clock),
        analyzer=analyzer,
        prefilter=Prefilter(settings.prefilter, settings.scoring),
        score_engine=ScoreEngine(settings.scoring),
        session_factory=session_factory,
        settings=settings.pipeline,
        clock=clock,
        acknowledge=acknowledge,
    )


class ReportOutput(BaseModel):
    report: DailyReport
    html_path: Path


class ReportNotFoundError(LookupError):
    pass


def generate_report(
    settings: Settings,
    session_factory: sessionmaker[Session],
    run_id: int | None = None,
    output: Path | None = None,
    clock: Clock = utc_now,
) -> ReportOutput:
    """Erzeugt den Report eines Laufs (Standard: letzter Lauf) und speichert ihn als HTML-Datei."""
    with session_scope(session_factory) as session:
        runs = CrawlRunRepository(session)
        run = runs.get(run_id) if run_id is not None else next(iter(runs.list_recent(limit=1)), None)
        if run is None:
            raise ReportNotFoundError(f"Kein Crawl-Lauf gefunden{f' mit ID {run_id}' if run_id else ''}")
        assert run.id is not None
        leads = LeadRepository(session).list_for_run(run.id)
    report = build_report(run, leads, settings.report, generated_at=clock())
    path = output or settings.report.output_dir / f"report-run-{run.id}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(report), encoding="utf-8")
    return ReportOutput(report=report, html_path=path)


def send_report(settings: Settings, report: DailyReport, clock: Clock = utc_now) -> DeliveryResult:
    mail = OutgoingMail(subject=report.subject, html=render_html(report), text=render_text(report))
    return create_mailer(settings.mail).send(mail, now=clock())


def send_test_mail(settings: Settings, recipients: list[str] | None = None, clock: Clock = utc_now) -> DeliveryResult:
    now = clock()
    text = f"Testnachricht des KI-Agenten für IT-Aufträge ({now:%d.%m.%Y %H:%M} UTC). Konfiguration funktioniert."
    mail = OutgoingMail(subject="Test-Mail", html=f"<p>{text}</p>", text=text)
    return create_mailer(settings.mail, recipients).send(mail, now=now)
