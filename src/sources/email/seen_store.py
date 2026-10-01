"""Merkt sich bereits verarbeitete Nachrichten (Deduplizierung über Läufe hinweg).

Der Connector kennt nur dieses Protokoll; die SQLite-Implementierung liegt in
``src.storage.repository.ProcessedEmailStore``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Literal, Protocol

from pydantic import AwareDatetime, BaseModel

MessageStatus = Literal["processed", "skipped", "failed"]


class ProcessedMessage(BaseModel):
    key: str
    status: MessageStatus
    source: str | None = None
    subject: str | None = None
    received_at: AwareDatetime | None = None
    item_count: int = 0


class SeenMessageStore(Protocol):
    def is_seen(self, key: str) -> bool: ...

    def mark_seen(self, messages: Iterable[ProcessedMessage], processed_at: datetime) -> None: ...


class InMemorySeenMessageStore:
    def __init__(self) -> None:
        self.messages: dict[str, ProcessedMessage] = {}

    def is_seen(self, key: str) -> bool:
        return key in self.messages

    def mark_seen(self, messages: Iterable[ProcessedMessage], processed_at: datetime) -> None:
        for message in messages:
            self.messages.setdefault(message.key, message)
