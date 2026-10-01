"""Versand des Reports an die konfigurierten Empfänger (nie an Kunden).

``dry_run`` (Standard): Die Mail wird vollständig aufgebaut und als ``.eml`` in
``mail.outbox_dir`` abgelegt; es wird nichts versendet. ``smtp``: Versand über SMTP.
"""

from __future__ import annotations

import logging
import re
import smtplib
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel

from src.config import MailSettings

logger = logging.getLogger(__name__)


class MailError(Exception):
    pass


class OutgoingMail(BaseModel):
    subject: str
    html: str
    text: str


class DeliveryResult(BaseModel):
    mode: Literal["dry_run", "smtp"]
    recipients: list[str]
    message_id: str
    path: Path | None = None
    """Bei dry_run: Pfad der abgelegten .eml-Datei."""


class Mailer(Protocol):
    def send(self, mail: OutgoingMail, now: datetime) -> DeliveryResult: ...


def build_message(mail: OutgoingMail, settings: MailSettings, recipients: list[str], now: datetime) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = f"{settings.subject_prefix} {mail.subject}".strip()
    message["From"] = settings.sender
    message["To"] = ", ".join(recipients)
    message["Date"] = format_datetime(now)
    message["Message-ID"] = make_msgid(domain=settings.sender.rpartition("@")[2] or "localhost")
    message.set_content(mail.text)
    message.add_alternative(mail.html, subtype="html")
    return message


class DryRunMailer:
    def __init__(self, settings: MailSettings, recipients: list[str]) -> None:
        self._settings = settings
        self._recipients = recipients

    def send(self, mail: OutgoingMail, now: datetime) -> DeliveryResult:
        message = build_message(mail, self._settings, self._recipients or ["(keine Empfänger konfiguriert)"], now)
        outbox = self._settings.outbox_dir
        outbox.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", mail.subject.lower()).strip("-")[:50]
        path = outbox / f"{now:%Y%m%d-%H%M%S}-{slug}.eml"
        path.write_bytes(bytes(message))
        logger.info("mail_dry_run", extra={"path": str(path), "recipients": self._recipients})
        return DeliveryResult(mode="dry_run", recipients=self._recipients, message_id=message["Message-ID"], path=path)


class SmtpMailer:
    def __init__(self, settings: MailSettings, recipients: list[str]) -> None:
        if not settings.smtp_host:
            raise MailError("mail.smtp_host ist nicht konfiguriert")
        if not recipients:
            raise MailError("Keine Empfänger konfiguriert (mail.recipients)")
        self._settings = settings
        self._recipients = recipients

    def send(self, mail: OutgoingMail, now: datetime) -> DeliveryResult:
        s = self._settings
        message = build_message(mail, s, self._recipients, now)
        try:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.timeout_seconds) as smtp:  # type: ignore[arg-type]
                if s.smtp_starttls:
                    smtp.starttls()
                if s.smtp_username and s.smtp_password:
                    smtp.login(s.smtp_username, s.smtp_password.get_secret_value())
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise MailError(f"SMTP-Versand fehlgeschlagen: {type(exc).__name__}: {exc}") from exc
        logger.info("mail_sent", extra={"recipients": self._recipients})
        return DeliveryResult(mode="smtp", recipients=self._recipients, message_id=message["Message-ID"])


def create_mailer(settings: MailSettings, recipients: list[str] | None = None) -> Mailer:
    """Empfänger stammen aus der Konfiguration (oder ausdrücklich per CLI für test-mail)."""
    targets = list(recipients) if recipients else list(settings.recipients)
    if settings.mode == "smtp":
        return SmtpMailer(settings, targets)
    return DryRunMailer(settings, targets)
