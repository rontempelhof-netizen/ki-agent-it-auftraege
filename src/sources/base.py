"""Gemeinsamer Vertrag aller Source Connectoren.

Ein Connector liefert ausschließlich ``RawSourceItem``s (untrusted input). Er
normalisiert, bewertet oder speichert nichts; das übernehmen spätere Pipeline-Schritte.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from src.domain.models import RawSourceItem

logger = logging.getLogger(__name__)


class SourceError(Exception):
    """Quelle ist nicht erreichbar oder grundlegend fehlerhaft konfiguriert."""


class FetchStats(BaseModel):
    messages_read: int = Field(0, ge=0)
    duplicates: int = Field(0, ge=0)
    unknown_sender: int = Field(0, ge=0)
    failed: int = Field(0, ge=0)
    items: int = Field(0, ge=0)


class FetchResult(BaseModel):
    connector: str
    items: list[RawSourceItem] = []
    warnings: list[str] = []
    stats: FetchStats = FetchStats()
    error: str | None = None
    """Gesetzt, wenn der Abruf insgesamt fehlgeschlagen ist."""
    processed_refs: list[str] = []
    """Opake Schlüssel der verarbeiteten Einträge für ``acknowledge``."""

    @property
    def ok(self) -> bool:
        return self.error is None


class SourceConnector(ABC):
    """Basisklasse aller Connectoren."""

    name: str

    @abstractmethod
    def fetch(self) -> FetchResult:
        """Liest neue Einträge der Quelle. Einzelne defekte Einträge werden als Warnung gemeldet."""

    def acknowledge(self, result: FetchResult) -> None:  # noqa: B027 - bewusst optional
        """Bestätigt die erfolgreiche Weiterverarbeitung, damit Einträge nicht erneut geliefert werden.

        Wird vom Aufrufer erst nach erfolgreicher Speicherung aufgerufen.
        """


def safe_fetch(connector: SourceConnector) -> FetchResult:
    """Führt ``fetch`` aus und kapselt Fehler, damit andere Quellen weiterlaufen."""
    try:
        return connector.fetch()
    except Exception as exc:  # noqa: BLE001 - Fehlerisolierung pro Quelle ist gewollt
        logger.exception("source_fetch_failed", extra={"connector": connector.name})
        return FetchResult(connector=connector.name, error=f"{type(exc).__name__}: {exc}")
