from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from src.main import main

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_without_command_prints_help(capsys):
    assert main([]) == 0
    assert "init-db" in capsys.readouterr().out


def test_show_config_outputs_effective_settings(write_yaml, monkeypatch, capsys):
    path = write_yaml("environment: test\n")
    monkeypatch.setenv("AGENT_LOGGING__LEVEL", "DEBUG")

    assert main(["--config", str(path), "show-config"]) == 0

    config = json.loads(capsys.readouterr().out)
    assert config["environment"] == "test"
    assert config["logging"]["level"] == "DEBUG"


def test_init_db_creates_database(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "data" / "agent.db"
    monkeypatch.setenv("AGENT_DATABASE__URL", f"sqlite:///{db_path.as_posix()}")

    assert main(["init-db"]) == 0

    assert db_path.is_file()
    assert "Datenbank initialisiert" in capsys.readouterr().out


def test_missing_config_file_returns_error_code(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "missing.yaml"), "show-config"]) == 2
    assert "Konfigurationsfehler" in capsys.readouterr().err


def test_module_entrypoint_runs_as_subprocess(tmp_path):
    db_path = tmp_path / "agent.db"
    result = subprocess.run(
        [sys.executable, "-m", "src.main", "init-db"],
        cwd=REPO_ROOT,
        env={
            "AGENT_DATABASE__URL": f"sqlite:///{db_path.as_posix()}",
            "AGENT_LOGGING__FORMAT": "json",
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "PATH": os.environ.get("PATH", ""),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert db_path.is_file()
    log_entry = json.loads(result.stderr.strip().splitlines()[0])
    assert log_entry["message"] == "app_started"
