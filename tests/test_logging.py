from __future__ import annotations

import io
import json
import logging

from src.config import LoggingSettings
from src.logging_setup import configure_logging


def test_json_logging_includes_extra_fields():
    stream = io.StringIO()
    configure_logging(LoggingSettings(level="INFO", format="json"), stream=stream)

    logging.getLogger("test").info("lead_found", extra={"source": "freelancermap", "count": 3})

    entry = json.loads(stream.getvalue().strip())
    assert entry["message"] == "lead_found"
    assert entry["level"] == "INFO"
    assert entry["logger"] == "test"
    assert entry["source"] == "freelancermap"
    assert entry["count"] == 3
    assert "timestamp" in entry


def test_log_level_filters_messages():
    stream = io.StringIO()
    configure_logging(LoggingSettings(level="WARNING", format="json"), stream=stream)

    logging.getLogger("test").info("hidden")
    logging.getLogger("test").warning("visible")

    lines = stream.getvalue().strip().splitlines()
    assert [json.loads(line)["message"] for line in lines] == ["visible"]


def test_text_logging_appends_key_value_pairs():
    stream = io.StringIO()
    configure_logging(LoggingSettings(level="INFO", format="text"), stream=stream)

    logging.getLogger("test").info("started", extra={"command": "init-db"})

    output = stream.getvalue()
    assert "INFO" in output and "started" in output
    assert "command='init-db'" in output


def test_reconfiguring_replaces_handler():
    configure_logging(LoggingSettings())
    configure_logging(LoggingSettings())

    assert len(logging.getLogger().handlers) == 1
