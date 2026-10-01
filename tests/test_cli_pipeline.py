"""CLI-Befehle crawl, report und test-mail mit Demo-Konfiguration (offline)."""

from __future__ import annotations

import pytest
import yaml

from src.main import main
from tests.pipeline_support import DEMO_INBOX, DEMO_RESPONSES


@pytest.fixture
def demo_config(tmp_path):
    config = {
        "database": {"url": f"sqlite:///{(tmp_path / 'agent.db').as_posix()}"},
        "logging": {"level": "WARNING", "format": "text"},
        "sources": {"email": {"mailbox_dir": str(DEMO_INBOX)}},
        "llm": {"provider": "fake", "fake_responses_file": str(DEMO_RESPONSES)},
        "report": {"output_dir": str(tmp_path / "reports")},
        "mail": {"mode": "dry_run", "outbox_dir": str(tmp_path / "outbox"), "recipients": ["ich@example.org"]},
    }
    path = tmp_path / "demo.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def test_crawl_creates_report_and_dry_run_mail(demo_config, tmp_path, capsys, no_network):
    assert main(["--config", str(demo_config), "crawl", "--send"]) == 0

    out = capsys.readouterr().out
    assert "Lauf #1: SUCCESS" in out and "A 1 B 2 C 1 Reject 2" in out
    assert (tmp_path / "reports" / "report-run-1.html").exists()
    assert "Mail (Dry-Run) abgelegt" in out and len(list((tmp_path / "outbox").glob("*.eml"))) == 1
    assert no_network == []


def test_crawl_options(demo_config, capsys):
    assert main(["--config", str(demo_config), "crawl", "--max-llm", "1", "--no-report", "--no-ack"]) == 0
    out = capsys.readouterr().out
    assert "LLM 1" in out and "ausstehend 4" in out and "Report:" not in out


def test_crawl_with_fake_llm_flag_overrides_provider(demo_config, tmp_path, capsys):
    config = demo_config.read_text(encoding="utf-8").replace("provider: fake", "provider: anthropic")
    demo_config.write_text(config, encoding="utf-8")

    assert main(["--config", str(demo_config), "crawl", "--fake-llm", "--no-report"]) == 0
    assert "A 1 B 2" in capsys.readouterr().out


def test_crawl_unknown_source(demo_config, capsys):
    assert main(["--config", str(demo_config), "crawl", "--source", "xing"]) == 2
    assert "Unbekannte Quelle 'xing'" in capsys.readouterr().err


def test_report_command(demo_config, tmp_path, capsys):
    main(["--config", str(demo_config), "crawl", "--no-report"])
    capsys.readouterr()

    output = tmp_path / "mein-report.html"
    assert main(["--config", str(demo_config), "report", "--run-id", "1", "--output", str(output), "--send"]) == 0

    out = capsys.readouterr().out
    assert f"Report: {output}" in out and "Shopware 6" in output.read_text(encoding="utf-8")
    assert "Mail (Dry-Run) abgelegt" in out


def test_report_without_runs(demo_config, capsys):
    assert main(["--config", str(demo_config), "report"]) == 2
    assert "Kein Crawl-Lauf gefunden" in capsys.readouterr().err


def test_test_mail_dry_run(demo_config, tmp_path, capsys):
    assert main(["--config", str(demo_config), "test-mail", "--to", "test@example.org"]) == 0

    out = capsys.readouterr().out
    assert "Mail (Dry-Run) abgelegt" in out and "nichts versendet" in out
    eml = next((tmp_path / "outbox").glob("*.eml")).read_text(encoding="utf-8")
    assert "To: test@example.org" in eml


def test_test_mail_smtp_misconfiguration(demo_config, capsys, monkeypatch):
    monkeypatch.setenv("AGENT_MAIL__MODE", "smtp")

    assert main(["--config", str(demo_config), "test-mail"]) == 2
    assert "smtp_host" in capsys.readouterr().err
