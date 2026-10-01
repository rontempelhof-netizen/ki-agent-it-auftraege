"""Robustes Parsen von RFC-822-Nachrichten (``.eml``) mit der Standardbibliothek.

Defekte Header, fehlende Felder, unbekannte Zeichensätze oder abgeschnittene
Multipart-Nachrichten führen nicht zum Abbruch: Fehlendes bleibt ``None`` und
wird in ``problems`` dokumentiert.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.message import EmailMessage, Message
from email.utils import getaddresses, parsedate_to_datetime


@dataclass(frozen=True)
class ParsedEmail:
    message_id: str | None
    sender_address: str | None
    sender_name: str | None
    subject: str | None
    date: datetime | None
    text_body: str | None
    html_body: str | None
    raw_digest: str
    problems: list[str] = field(default_factory=list)

    @property
    def has_content(self) -> bool:
        return bool((self.text_body or "").strip() or (self.html_body or "").strip())

    @property
    def has_headers(self) -> bool:
        """Mindestens ein identifizierender Header ist vorhanden (sonst keine echte Mail)."""
        return any((self.message_id, self.sender_address, self.subject, self.date))

    @property
    def key(self) -> str:
        """Stabiler Deduplizierungsschlüssel: Message-ID, sonst Inhalts-Hash."""
        if self.message_id:
            return f"mid:{self.message_id}"
        content = "\x1f".join(
            [self.sender_address or "", self.subject or "", self.date.isoformat() if self.date else "",
             self.text_body or "", self.html_body or ""]
        )
        return "sha256:" + hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


def _header(msg: Message, name: str, problems: list[str]) -> str | None:
    try:
        value = msg.get(name)
    except Exception as exc:  # noqa: BLE001 - defekte Header dürfen nicht abbrechen
        problems.append(f"Header {name} nicht lesbar: {exc}")
        return None
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _normalize_message_id(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().strip("<>").strip().lower() or None


def _parse_sender(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    addresses = [(name, addr) for name, addr in getaddresses([value]) if addr and "@" in addr]
    if not addresses:
        return None, None
    name, address = addresses[0]
    return address.strip().lower(), (name.strip() or None)


def _parse_date(value: str | None, problems: list[str]) -> datetime | None:
    if not value:
        problems.append("Date-Header fehlt")
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        problems.append(f"Date-Header ungültig: {value!r}")
        return None
    if parsed.tzinfo is None:  # "-0000": Zeitzone unbekannt -> als UTC interpretieren
        problems.append("Date-Header ohne Zeitzone, als UTC interpretiert")
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _decode_part(part: Message, problems: list[str]) -> str | None:
    try:
        if isinstance(part, EmailMessage):
            content = part.get_content()
            if isinstance(content, str):
                return content
    except (LookupError, UnicodeError, ValueError, AssertionError) as exc:
        problems.append(f"Inhalt ({part.get_content_type()}) mit Ersatzdekodierung gelesen: {exc}")
    payload = part.get_payload(decode=True)
    if isinstance(payload, bytes):
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except LookupError:
            return payload.decode("utf-8", errors="replace")
    return None


def _bodies(msg: Message, problems: list[str]) -> tuple[str | None, str | None]:
    text_body: str | None = None
    html_body: str | None = None
    for part in msg.walk():
        if part.is_multipart():
            continue
        if (part.get_content_disposition() or "") == "attachment":
            continue
        content_type = part.get_content_type()
        if content_type == "text/plain" and text_body is None:
            text_body = _decode_part(part, problems)
        elif content_type == "text/html" and html_body is None:
            html_body = _decode_part(part, problems)
    return text_body, html_body


def parse_email(raw: bytes) -> ParsedEmail:
    problems: list[str] = []
    msg = message_from_bytes(raw, policy=policy.default)
    problems.extend(f"MIME-Defekt: {type(d).__name__}" for part in msg.walk() for d in part.defects)

    sender_address, sender_name = _parse_sender(_header(msg, "From", problems))
    if sender_address is None:
        problems.append("Absender fehlt oder ist ungültig")
    text_body, html_body = _bodies(msg, problems)

    return ParsedEmail(
        message_id=_normalize_message_id(_header(msg, "Message-ID", problems)),
        sender_address=sender_address,
        sender_name=sender_name,
        subject=_header(msg, "Subject", problems),
        date=_parse_date(_header(msg, "Date", problems), problems),
        text_body=text_body,
        html_body=html_body,
        raw_digest=hashlib.sha256(raw).hexdigest(),
        problems=problems,
    )
