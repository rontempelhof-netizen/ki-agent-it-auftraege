"""Deklarative Basis und gemeinsame Spaltentypen.

Migrationsfähigkeit (z. B. Alembic):
- Alle Tabellen hängen an ``Base.metadata`` -> später ``target_metadata = Base.metadata``.
- Feste Namenskonvention für Constraints/Indizes, damit Autogenerate stabile Namen
  erzeugt und SQLite-Batch-Migrationen Constraints adressieren können.
- Enums werden als String gespeichert (keine nativen Enum-Typen/CHECK-Constraints),
  neue Enum-Werte erfordern daher keine Schemamigration.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, MetaData
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Basisklasse aller ORM-Modelle."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UTCDateTime(TypeDecorator[datetime]):
    """Speichert zeitzonenbehaftete Zeitpunkte als UTC und liefert sie mit UTC zurück.

    SQLite kennt keine Zeitzonen; ohne diesen Typ gingen Offsets verloren.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetime kann nicht gespeichert werden; Zeitzone erforderlich")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)
