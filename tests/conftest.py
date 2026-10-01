from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Isoliert Tests von lokalen AGENT_*-Variablen, .env und config/ des Entwicklers."""
    for key in list(os.environ):
        if key.startswith("AGENT_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def restore_root_logger():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.fixture
def write_yaml(tmp_path: Path):
    def _write(content: str, name: str = "settings.yaml") -> Path:
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        return path

    return _write
