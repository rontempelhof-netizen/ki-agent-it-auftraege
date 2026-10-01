"""Postfach-Abstraktion. Offline: Verzeichnis mit ``.eml``-Dateien.

Eine produktive IMAP/Gmail-Anbindung implementiert später dasselbe ``Mailbox``-Protokoll.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from src.sources.base import SourceError


@dataclass(frozen=True)
class MailboxMessage:
    ref: str
    """Herkunft der Nachricht (z. B. Dateiname), nur für Diagnose."""
    raw: bytes


class Mailbox(Protocol):
    def iter_messages(self) -> Iterable[MailboxMessage]: ...


class EmlDirectoryMailbox:
    def __init__(self, directory: Path | str, pattern: str = "*.eml") -> None:
        self._directory = Path(directory)
        self._pattern = pattern

    def iter_messages(self) -> Iterator[MailboxMessage]:
        if not self._directory.is_dir():
            raise SourceError(f"Mail-Verzeichnis nicht gefunden: {self._directory}")
        for path in sorted(self._directory.glob(self._pattern)):
            if path.is_file():
                yield MailboxMessage(ref=path.name, raw=path.read_bytes())


class InMemoryMailbox:
    def __init__(self, messages: Iterable[MailboxMessage | bytes]) -> None:
        self._messages = [
            m if isinstance(m, MailboxMessage) else MailboxMessage(ref=f"message-{i}", raw=m)
            for i, m in enumerate(messages)
        ]

    def iter_messages(self) -> Iterator[MailboxMessage]:
        return iter(self._messages)
