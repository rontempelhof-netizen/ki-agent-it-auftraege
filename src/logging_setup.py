"""Strukturiertes Logging auf Basis der Standardbibliothek.

Zusätzliche Felder werden über ``extra`` übergeben, z. B.
``logger.info("db_initialized", extra={"db_url": url})``.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

from src.config import LoggingSettings

# Attribute, die jeder LogRecord besitzt und nicht als "extra" ausgegeben werden.
_RESERVED_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys() | {"message", "asctime"}
)


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in record.__dict__.items() if k not in _RESERVED_ATTRS}


class JsonFormatter(logging.Formatter):
    """Formatiert Log-Einträge als eine JSON-Zeile pro Eintrag."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(_extra_fields(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class KeyValueFormatter(logging.Formatter):
    """Menschenlesbares Format mit angehängten ``key=value``-Feldern."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-8s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        extras = " ".join(f"{k}={v!r}" for k, v in _extra_fields(record).items())
        return f"{line} {extras}" if extras else line


def configure_logging(settings: LoggingSettings, stream: TextIO | None = None) -> None:
    """Konfiguriert den Root-Logger. Mehrfacher Aufruf ersetzt den Handler."""
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter() if settings.format == "json" else KeyValueFormatter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(settings.level)
