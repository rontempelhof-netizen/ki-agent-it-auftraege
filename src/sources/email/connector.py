"""EmailSourceConnector: Projektbenachrichtigungen aus einem Postfach -> RawSourceItems.

Verantwortung: Nachrichten lesen, parsen, deduplizieren, einer Quelle zuordnen und in
Projektblöcke zerlegen. Keine Normalisierung, kein Scoring, keine Persistenz.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from src.config import EmailSourceProfile, EmailSourceSettings
from src.domain.models import RawSourceItem
from src.sources.base import FetchResult, SourceConnector
from src.sources.email.extract import ExtractedBody, html_to_body, text_to_body
from src.sources.email.mailbox import Mailbox, MailboxMessage
from src.sources.email.parser import ParsedEmail, parse_email
from src.sources.email.projects import ProjectBlock, match_profile, split_projects
from src.sources.email.seen_store import InMemorySeenMessageStore, ProcessedMessage, SeenMessageStore

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _short_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


class EmailSourceConnector(SourceConnector):
    name = "email"

    def __init__(
        self,
        settings: EmailSourceSettings,
        mailbox: Mailbox,
        seen_store: SeenMessageStore | None = None,
        clock: Clock = _utc_now,
    ) -> None:
        self._settings = settings
        self._mailbox = mailbox
        self._seen_store = seen_store or InMemorySeenMessageStore()
        self._clock = clock
        self._pending: dict[str, ProcessedMessage] = {}

    # --- öffentlicher Vertrag ---------------------------------------------

    def fetch(self) -> FetchResult:
        result = FetchResult(connector=self.name)
        seen_this_run: set[str] = set()
        self._pending = {}
        for message in self._mailbox.iter_messages():
            result.stats.messages_read += 1
            try:
                self._process(message, result, seen_this_run)
            except Exception as exc:  # noqa: BLE001 - eine defekte Mail darf den Lauf nicht stoppen
                logger.exception("email_processing_failed", extra={"ref": message.ref})
                self._fail(message, result, f"unerwarteter Fehler: {type(exc).__name__}: {exc}")
        result.stats.items = len(result.items)
        result.processed_refs = list(self._pending)
        logger.info("email_fetch_finished", extra=result.stats.model_dump())
        return result

    def acknowledge(self, result: FetchResult) -> None:
        records = [self._pending[key] for key in result.processed_refs if key in self._pending]
        self._seen_store.mark_seen(records, processed_at=self._clock())
        for record in records:
            self._pending.pop(record.key, None)

    # --- Verarbeitung je Nachricht ------------------------------------------

    def _process(self, message: MailboxMessage, result: FetchResult, seen_this_run: set[str]) -> None:
        parsed = parse_email(message.raw)
        key = parsed.key
        if key in seen_this_run or self._seen_store.is_seen(key):
            result.stats.duplicates += 1
            return
        seen_this_run.add(key)
        label = f"{message.ref} ({parsed.subject or 'ohne Betreff'})"

        if not parsed.has_headers:
            self._reject(parsed, label, result, "keine gültigen Mail-Header, keine E-Mail-Nachricht")
            return
        if not parsed.has_content:
            self._reject(parsed, label, result, "unvollständige Mail ohne Inhalt übersprungen")
            return

        profile = match_profile(self._settings.profiles, parsed.sender_address, parsed.subject)
        if profile is None:
            self._handle_unknown(parsed, label, result)
            return
        for problem in parsed.problems:
            result.warnings.append(f"{label}: {problem}")

        blocks, body_kind = self._extract_projects(parsed, profile)
        if not blocks:
            result.warnings.append(f"{label}: keine Projektlinks erkannt, gesamte Mail als ein Eintrag übernommen")
            items = [self._whole_mail_item(parsed, profile.name)]
        else:
            items = [self._block_item(parsed, profile.name, block, i, len(blocks), body_kind) for i, block in enumerate(blocks)]
        result.items.extend(items)
        self._remember(parsed, "processed", profile.name, len(items))

    def _handle_unknown(self, parsed: ParsedEmail, label: str, result: FetchResult) -> None:
        result.stats.unknown_sender += 1
        sender = parsed.sender_address or "unbekannt"
        if self._settings.unknown_sender_policy == "skip":
            result.warnings.append(f"{label}: unbekannter Absender {sender}, übersprungen")
            self._remember(parsed, "skipped", None, 0)
            return
        result.warnings.append(f"{label}: unbekannter Absender {sender}, als generischer Eintrag übernommen")
        result.items.append(self._whole_mail_item(parsed, self._settings.unknown_source_name))
        self._remember(parsed, "processed", self._settings.unknown_source_name, 1)

    def _reject(self, parsed: ParsedEmail, label: str, result: FetchResult, reason: str) -> None:
        result.stats.failed += 1
        result.warnings.append(f"{label}: {reason}")
        self._remember(parsed, "failed", None, 0)

    def _fail(self, message: MailboxMessage, result: FetchResult, reason: str) -> None:
        result.stats.failed += 1
        result.warnings.append(f"{message.ref}: {reason}")
        key = "sha256:" + hashlib.sha256(message.raw).hexdigest()
        self._pending[key] = ProcessedMessage(key=key, status="failed")

    def _remember(self, parsed: ParsedEmail, status: str, source: str | None, item_count: int) -> None:
        self._pending[parsed.key] = ProcessedMessage(
            key=parsed.key,
            status=status,  # type: ignore[arg-type]
            source=source,
            subject=parsed.subject,
            received_at=parsed.date,
            item_count=item_count,
        )

    # --- Extraktion -------------------------------------------------------

    def _bodies(self, parsed: ParsedEmail) -> list[tuple[str, ExtractedBody]]:
        bodies: list[tuple[str, ExtractedBody]] = []
        if parsed.html_body and parsed.html_body.strip():
            bodies.append(("html", html_to_body(parsed.html_body)))
        if parsed.text_body and parsed.text_body.strip():
            bodies.append(("text", text_to_body(parsed.text_body)))
        return bodies

    def _extract_projects(self, parsed: ParsedEmail, profile: EmailSourceProfile) -> tuple[list[ProjectBlock], str | None]:
        """HTML bevorzugt (aussagekräftige Linktexte), Text als Fallback."""
        for kind, body in self._bodies(parsed):
            layout = profile.html_layout if kind == "html" else profile.text_layout
            blocks = split_projects(body, profile, layout, self._settings.generic_link_texts)
            if blocks:
                return blocks, kind
        return [], None

    # --- RawSourceItems ---------------------------------------------------

    def _base_metadata(self, parsed: ParsedEmail) -> dict[str, str]:
        metadata = {
            "channel": "email",
            "message_key": parsed.key,
            "mail_from": parsed.sender_address or "",
            "mail_subject": parsed.subject or "",
            "mail_date": parsed.date.isoformat() if parsed.date else "",
        }
        if parsed.date is None:
            metadata["received_at_fallback"] = "true"
        return metadata

    def _received_at(self, parsed: ParsedEmail) -> datetime:
        return parsed.date or self._clock()

    def _block_item(
        self, parsed: ParsedEmail, source: str, block: ProjectBlock, index: int, count: int, body_kind: str | None
    ) -> RawSourceItem:
        return RawSourceItem(
            source=source,
            source_item_id=block.item_id,
            source_url=block.url,
            received_at=self._received_at(parsed),
            title=block.title,
            body_text=block.text or None,
            metadata=self._base_metadata(parsed)
            | {
                "body_kind": body_kind or "",
                "project_index": str(index),
                "project_count": str(count),
                "links": "\n".join(block.links),
            },
        )

    def _whole_mail_item(self, parsed: ParsedEmail, source: str) -> RawSourceItem:
        bodies = self._bodies(parsed)
        text_body = next((b for kind, b in bodies if kind == "text"), None) or (bodies[0][1] if bodies else None)
        links = tuple(dict.fromkeys(link.url for _, body in bodies for link in body.links))
        return RawSourceItem(
            source=source,
            source_item_id=f"mail-{_short_hash(parsed.key)}",
            received_at=self._received_at(parsed),
            title=parsed.subject,
            body_text=text_body.text if text_body else None,
            body_html=parsed.html_body,
            metadata=self._base_metadata(parsed) | {"project_count": "0", "links": "\n".join(links)},
        )
