"""SQLAlchemy-Engine, Session-Factory und DB-Initialisierung.

Schemadefinitionen liegen ausschließlich in ``src.storage.orm`` (an ``Base.metadata``).
``init_db`` ist der einzige Ort, an dem das Schema angelegt wird; wird später ein
Migrationswerkzeug (z. B. Alembic) eingeführt, ersetzt es nur diese Funktion.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, make_url, select
from sqlalchemy.orm import Session, sessionmaker

from src.config import DatabaseSettings
from src.storage.base import Base
from src.storage.orm import AppMeta

SCHEMA_VERSION = "3"

logger = logging.getLogger(__name__)


def _ensure_sqlite_directory(url: str) -> None:
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        return
    database = parsed.database
    if database and database != ":memory:" and not database.startswith("file:"):
        Path(database).parent.mkdir(parents=True, exist_ok=True)


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_db_engine(settings: DatabaseSettings) -> Engine:
    """Erzeugt die Engine; legt bei SQLite das Zielverzeichnis an."""
    _ensure_sqlite_directory(settings.url)
    engine = create_engine(settings.url, echo=settings.echo)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Transaktionsrahmen: Commit bei Erfolg, Rollback bei Fehler."""
    with session_factory() as session, session.begin():
        yield session


def init_db(engine: Engine) -> str:
    """Legt fehlende Tabellen an (idempotent) und gibt die Schema-Version zurück.

    ``create_all`` ändert keine bestehenden Tabellen. Spaltenänderungen an
    bestehenden Datenbanken erfordern ein Migrationswerkzeug.
    """
    Base.metadata.create_all(engine)
    with Session(engine) as session, session.begin():
        if session.get(AppMeta, "initialized_at") is None:
            session.add(AppMeta(key="initialized_at", value=datetime.now(UTC).isoformat()))
        session.merge(AppMeta(key="schema_version", value=SCHEMA_VERSION))
    version = get_schema_version(engine) or SCHEMA_VERSION
    logger.info("db_initialized", extra={"db_url": engine.url.render_as_string(), "schema_version": version})
    return version


def get_schema_version(engine: Engine) -> str | None:
    with Session(engine) as session:
        return session.scalar(select(AppMeta.value).where(AppMeta.key == "schema_version"))
