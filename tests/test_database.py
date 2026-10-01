from __future__ import annotations

from sqlalchemy import inspect, text

from src.config import DatabaseSettings
from src.storage.database import SCHEMA_VERSION, create_db_engine, create_session_factory, get_schema_version, init_db


def test_init_db_creates_file_directory_and_meta_table(tmp_path):
    db_path = tmp_path / "nested" / "dir" / "agent.db"
    engine = create_db_engine(DatabaseSettings(url=f"sqlite:///{db_path.as_posix()}"))

    version = init_db(engine)

    assert db_path.is_file()
    assert version == SCHEMA_VERSION
    assert "app_meta" in inspect(engine).get_table_names()
    engine.dispose()


def test_init_db_is_idempotent(tmp_path):
    engine = create_db_engine(DatabaseSettings(url=f"sqlite:///{(tmp_path / 'a.db').as_posix()}"))

    init_db(engine)
    init_db(engine)

    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM app_meta WHERE key = 'schema_version'")).scalar_one()
    assert count == 1
    assert get_schema_version(engine) == SCHEMA_VERSION
    engine.dispose()


def test_init_db_upgrades_database_from_task_01(tmp_path):
    """Eine DB aus Task 01 (nur app_meta, Version 1) erhält die neuen Tabellen."""
    engine = create_db_engine(DatabaseSettings(url=f"sqlite:///{(tmp_path / 'old.db').as_posix()}"))
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE app_meta (key VARCHAR(64) PRIMARY KEY, value VARCHAR(255) NOT NULL)"))
        conn.execute(text("INSERT INTO app_meta VALUES ('schema_version', '1'), ('initialized_at', 'x')"))

    init_db(engine)

    assert {"leads", "lead_score_details"} <= set(inspect(engine).get_table_names())
    assert get_schema_version(engine) == SCHEMA_VERSION
    engine.dispose()


def test_in_memory_database_and_foreign_keys_enabled():
    engine = create_db_engine(DatabaseSettings(url="sqlite:///:memory:"))
    init_db(engine)

    session_factory = create_session_factory(engine)
    with session_factory() as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    engine.dispose()


def test_outdated_schema_is_detected(tmp_path):
    """create_all rüstet keine Spalten nach; init_db meldet das klar statt später zu scheitern."""
    import pytest

    from src.storage.database import SchemaMismatchError

    engine = create_db_engine(DatabaseSettings(url=f"sqlite:///{(tmp_path / 'v3.db').as_posix()}"))
    init_db(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE leads DROP COLUMN llm_model"))  # Stand Schema-Version 3

    with pytest.raises(SchemaMismatchError, match="leads.llm_model"):
        init_db(engine)
    engine.dispose()


def test_changed_nullability_is_detected(tmp_path):
    import pytest

    from src.storage.database import SchemaMismatchError

    engine = create_db_engine(DatabaseSettings(url=f"sqlite:///{(tmp_path / 'v4.db').as_posix()}"))
    with engine.begin() as conn:  # Schema-Version 4: lead_class war NOT NULL
        conn.execute(text("CREATE TABLE leads (id INTEGER PRIMARY KEY, lead_class VARCHAR(8) NOT NULL)"))
    with pytest.raises(SchemaMismatchError, match="leads.lead_class muss NULL erlauben"):
        init_db(engine)
    engine.dispose()
