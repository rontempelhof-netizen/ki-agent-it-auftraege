"""Read-only Gmail API mailbox adapter.

Only labels.list, messages.list, and messages.get(format='raw') are called. No
message, label, or read/unread state is ever modified. OAuth setup is explicit
and runs on the user's own computer, not inside a scheduled crawl.
"""

from __future__ import annotations

import base64
import binascii
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from src.config import GmailSettings
from src.sources.base import SourceError
from src.sources.email.mailbox import MailboxMessage

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


def _require_google_libraries() -> None:
    try:
        import google.auth.transport.requests  # noqa: F401
        import google.oauth2.credentials  # noqa: F401
        import google_auth_oauthlib.flow  # noqa: F401
        import googleapiclient.discovery  # noqa: F401
    except ImportError as exc:
        raise SourceError('Gmail-Abhängigkeiten fehlen: pip install -e ".[gmail]"') from exc


def _save_private_token(path: Path, token_json: str) -> None:
    """Restrictive file mode on POSIX; parent is also private when created."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(token_json)
    except BaseException:
        raise


def authorize_gmail(settings: GmailSettings) -> None:
    """One-time, interactive local authorization. Never called by a crawl."""
    _require_google_libraries()
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not settings.client_secret_file.is_file():
        raise SourceError(
            f"Gmail-OAuth-Clientdatei fehlt: {settings.client_secret_file}. "
            "Google Cloud Desktop-OAuth-Client lokal ablegen (nicht in GitHub hochladen)."
        )
    flow = InstalledAppFlow.from_client_secrets_file(
        str(settings.client_secret_file), scopes=[GMAIL_READONLY_SCOPE]
    )
    credentials = flow.run_local_server(host="localhost", port=0)
    _save_private_token(settings.token_file, credentials.to_json())


def build_gmail_service(settings: GmailSettings) -> Any:
    """Load previously authorized read-only credentials (no browser prompt)."""
    token_file = settings.token_file
    if not token_file.is_file():
        raise SourceError(
            f"Gmail ist noch nicht autorisiert: {token_file}. "
            "Einmalig lokal 'python -m src.main gmail-auth' ausführen."
        )
    _require_google_libraries()
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    credentials = Credentials.from_authorized_user_file(
        str(token_file), scopes=[GMAIL_READONLY_SCOPE]
    )
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        _save_private_token(token_file, credentials.to_json())
    if not credentials.valid:
        raise SourceError(
            "Gespeicherte Gmail-Anmeldung ist ungültig. "
            "Erneut lokal 'python -m src.main gmail-auth' ausführen."
        )
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


class GmailMailbox:
    """Gmail messages in a configured label -> raw RFC-822 MailboxMessage.

    A service object can be injected for isolated tests (no Google dependency).
    """

    def __init__(self, settings: GmailSettings, service: Any | None = None) -> None:
        self._settings = settings
        self._service = service

    def _api(self) -> Any:
        if self._service is None:
            self._service = build_gmail_service(self._settings)
        return self._service

    def _label_id(self) -> str:
        response = self._api().users().labels().list(userId="me").execute()
        for label in response.get("labels", []):
            if label.get("name") == self._settings.label:
                return str(label["id"])
        raise SourceError(
            f"Gmail-Label '{self._settings.label}' nicht gefunden. "
            "Namen einschließlich Schrägstrichen genau prüfen."
        )

    def iter_messages(self) -> Iterator[MailboxMessage]:
        label_id = self._label_id()
        messages_api = self._api().users().messages()
        remaining = self._settings.max_messages
        page_token: str | None = None
        while remaining:
            parameters: dict[str, Any] = {
                "userId": "me",
                "labelIds": [label_id],
                "maxResults": min(remaining, 500),
            }
            if page_token:
                parameters["pageToken"] = page_token
            response = messages_api.list(**parameters).execute()
            messages = response.get("messages", [])
            if not messages:
                break
            for summary in messages:
                if not remaining:
                    return
                message_id = summary["id"]
                payload = messages_api.get(
                    userId="me", id=message_id, format="raw"
                ).execute()
                encoded = payload.get("raw")
                if not isinstance(encoded, str) or not encoded:
                    raise SourceError(f"Gmail-Nachricht {message_id} enthält keine RFC-822-Rohdaten")
                try:
                    raw = base64.b64decode(
                        encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
                    )
                except (ValueError, binascii.Error) as exc:
                    raise SourceError(
                        f"Gmail-Nachricht {message_id} enthält ungültige RFC-822-Rohdaten"
                    ) from exc
                yield MailboxMessage(ref=f"gmail:{message_id}", raw=raw)
                remaining -= 1
            page_token = response.get("nextPageToken")
            if not page_token:
                break
