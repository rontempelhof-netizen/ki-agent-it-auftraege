"""SQLite-Deduplizierung eingelesener Nachrichten über mehrere Läufe."""

from __future__ import annotations

from src.config import DatabaseSettings, EmailSourceSettings
from src.sources.email import EmailSourceConnector, EmlDirectoryMailbox
from src.storage.database import create_db_engine, create_session_factory, init_db, session_scope
from src.storage.repository import ProcessedEmailStore
from tests.email_fixtures import EMAIL_DIR, FIXED_NOW, fixed_clock


def test_processed_messages_survive_restart(tmp_path):
    engine = create_db_engine(DatabaseSettings(url=f"sqlite:///{(tmp_path / 'db.sqlite').as_posix()}"))
    init_db(engine)
    factory = create_session_factory(engine)

    with session_scope(factory) as session:  # Lauf 1
        connector = EmailSourceConnector(
            EmailSourceSettings(), EmlDirectoryMailbox(EMAIL_DIR), ProcessedEmailStore(session), clock=fixed_clock
        )
        first = connector.fetch()
        connector.acknowledge(first)

    with session_scope(factory) as session:  # Lauf 2: neue Session, neuer Connector
        store = ProcessedEmailStore(session)
        connector = EmailSourceConnector(EmailSourceSettings(), EmlDirectoryMailbox(EMAIL_DIR), store, clock=fixed_clock)
        second = connector.fetch()
        record = store.get("mid:20260930070211.4f2a1c@mailer.freelancermap.de")

    assert len(first.items) == 7
    assert second.items == [] and second.stats.duplicates == 8
    assert record is not None
    assert (record.status, record.source, record.item_count) == ("processed", "freelancermap", 3)
    engine.dispose()


def test_mark_seen_is_idempotent(tmp_path):
    from src.sources.email import ProcessedMessage

    engine = create_db_engine(DatabaseSettings(url=f"sqlite:///{(tmp_path / 'db.sqlite').as_posix()}"))
    init_db(engine)
    with session_scope(create_session_factory(engine)) as session:
        store = ProcessedEmailStore(session)
        message = ProcessedMessage(key="mid:x@example", status="skipped")
        store.mark_seen([message], FIXED_NOW)
        store.mark_seen([message], FIXED_NOW)

        assert store.is_seen("mid:x@example") and not store.is_seen("mid:y@example")
    engine.dispose()
