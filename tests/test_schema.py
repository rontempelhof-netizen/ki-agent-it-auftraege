"""Schema-Eigenschaften, die eine spätere Migration (z. B. Alembic) ermöglichen."""

from __future__ import annotations

from sqlalchemy import Enum as SAEnum
from sqlalchemy import inspect

from src.config import DatabaseSettings
from src.storage.base import NAMING_CONVENTION, Base
from src.storage.database import create_db_engine, init_db

REQUIRED_TABLES = {
    "leads", "lead_sources", "lead_score_details", "crawl_runs", "feedback", "app_meta", "processed_emails",
}


def test_all_tables_registered_on_single_metadata():
    import src.storage.orm  # noqa: F401  (Registrierung der Tabellen)

    assert REQUIRED_TABLES <= set(Base.metadata.tables)
    assert Base.metadata.naming_convention == NAMING_CONVENTION


def test_all_constraints_and_indexes_have_deterministic_names():
    for table in Base.metadata.tables.values():
        for constraint in table.constraints:
            assert constraint.name, f"{table.name}: unbenannter Constraint {constraint}"
            assert str(constraint.name).split("_")[0] in {"pk", "fk", "uq", "ck"}
        for index in table.indexes:
            assert str(index.name).startswith("ix_")


def test_no_native_enum_columns():
    for table in Base.metadata.tables.values():
        for column in table.columns:
            assert not isinstance(column.type, SAEnum), f"{table.name}.{column.name} nutzt nativen Enum-Typ"


def test_init_db_creates_schema_with_named_constraints(tmp_path):
    engine = create_db_engine(DatabaseSettings(url=f"sqlite:///{(tmp_path / 's.db').as_posix()}"))
    init_db(engine)
    inspector = inspect(engine)

    assert REQUIRED_TABLES <= set(inspector.get_table_names())
    assert {u["name"] for u in inspector.get_unique_constraints("leads")} == {"uq_leads_source_source_id"}
    assert {fk["name"] for fk in inspector.get_foreign_keys("lead_score_details")} == {
        "fk_lead_score_details_lead_id_leads"
    }
    engine.dispose()
