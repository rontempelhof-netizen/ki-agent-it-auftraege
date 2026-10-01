"""Gemeinsame Hilfen für Pipeline-Tests: Demo-Postfach, Fake-LLM, temporäre Datenbank."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.app import build_pipeline
from src.config import Settings
from src.llm import FakeLLMProvider
from src.pipeline.runner import Pipeline
from src.sources.base import SourceConnector
from src.storage.database import create_db_engine, create_session_factory, init_db, session_scope
from src.storage.repository import LeadRepository

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = REPO_ROOT / "examples" / "demo"
DEMO_INBOX = DEMO_DIR / "inbox"
DEMO_RESPONSES = DEMO_DIR / "fake_llm_responses.json"

OPEN_ENGINES: list[Engine] = []
"""Von ``make_env`` erzeugte Engines; werden nach jedem Test in conftest.py freigegeben."""


class StepClock:
    """Deterministische Uhr: jeder Aufruf eine Sekunde später."""

    def __init__(self, start: datetime = datetime(2026, 10, 1, 7, 30, tzinfo=UTC)) -> None:
        self.now = start

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


def demo_settings(tmp_path: Path, **pipeline: object) -> Settings:
    return Settings.model_validate(
        {
            "database": {"url": f"sqlite:///{(tmp_path / 'agent.db').as_posix()}"},
            "sources": {"email": {"mailbox_dir": str(DEMO_INBOX)}},
            "llm": {"provider": "fake", "fake_responses_file": str(DEMO_RESPONSES)},
            "pipeline": pipeline,
            "report": {"output_dir": str(tmp_path / "reports")},
            "mail": {"mode": "dry_run", "outbox_dir": str(tmp_path / "outbox"), "recipients": ["ich@example.org"]},
        }
    )


@dataclass
class Env:
    settings: Settings
    factory: sessionmaker[Session]
    clock: StepClock

    def pipeline(
        self,
        provider: FakeLLMProvider,
        connectors: list[SourceConnector] | None = None,
        acknowledge: bool = True,
    ) -> Pipeline:
        return build_pipeline(self.settings, self.factory, provider=provider, connectors=connectors,
                              acknowledge=acknowledge, clock=self.clock)

    def leads(self) -> dict[str, object]:
        with session_scope(self.factory) as session:
            return {lead.source_id: lead for lead in LeadRepository(session).list_leads()}


def make_env(tmp_path: Path, **pipeline: object) -> Env:
    settings = demo_settings(tmp_path, **pipeline)
    engine = create_db_engine(settings.database)
    OPEN_ENGINES.append(engine)
    init_db(engine)
    return Env(settings=settings, factory=create_session_factory(engine), clock=StepClock())


def demo_provider() -> FakeLLMProvider:
    return FakeLLMProvider.from_responses_file(DEMO_RESPONSES)


# Erwartete Ergebnisse des Demo-Postfachs (source_id -> (Klasse, Score, Verarbeitungsstatus))
EXPECTED = {
    "2981734": ("A", 93, "analyzed"),       # Shopware-Bugfix
    "2982210": ("B", 79, "analyzed"),       # CSV-Import nach MS SQL
    "1187245": ("B", 73, "analyzed"),       # API-Anbindung Warenwirtschaft
    "1188003": ("C", 52, "analyzed"),       # Datenmigration
    "2979988": ("REJECT", 20, "analyzed"),  # SAP: Hard Fails nach LLM
    "2984001": ("REJECT", 0, "prefiltered"),  # Festanstellung: Prefilter, kein LLM
}
