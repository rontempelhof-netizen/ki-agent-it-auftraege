"""Offline contract tests for the read-only Gmail adapter (no Google account needed)."""

from __future__ import annotations

import base64
import os

import pytest

from src.config import EmailSourceSettings, GmailSettings, load_settings
from src.sources.base import SourceError
from src.sources.email.connector import EmailSourceConnector
from src.sources.email.gmail_mailbox import (
    GMAIL_READONLY_SCOPE,
    GmailMailbox,
    _save_private_token,
    build_gmail_service,
)
from src.sources.email.seen_store import InMemorySeenMessageStore


def mail(message_id: str, project_id: int) -> bytes:
    return (
        f"From: freelancermap Service <office@freelancermap.de>\r\n"
        f"Subject: 1 neues Projekt\r\n"
        f"Message-ID: <{message_id}@freelancermap.de>\r\n"
        f"Date: Wed, 04 Nov 2026 08:30:00 +0100\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        f"Projekt {project_id}\r\n"
        f"https://www.freelancermap.de/nproj/{project_id}.html?utm_source=test\r\n"
    ).encode("utf-8")


class Request:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class Labels:
    def __init__(self, names):
        self.names = names
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return Request({"labels": [
            {"id": f"Label_{i}", "name": name} for i, name in enumerate(self.names)
        ]})


class Messages:
    def __init__(self, items):
        self.items = items
        self.list_calls = []
        self.get_calls = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        start = int(kwargs.get("pageToken", "0"))
        max_results = kwargs["maxResults"]
        ids = list(self.items)[start:start + max_results]
        next_offset = start + len(ids)
        response = {"messages": [{"id": key} for key in ids]}
        if next_offset < len(self.items):
            response["nextPageToken"] = str(next_offset)
        return Request(response)

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        raw = self.items[kwargs["id"]]
        if isinstance(raw, bytes):
            raw = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        return Request({"raw": raw})


class Users:
    def __init__(self, names, messages):
        self.labels_api = Labels(names)
        self.messages_api = Messages(messages)

    def labels(self):
        return self.labels_api

    def messages(self):
        return self.messages_api


class Service:
    def __init__(self, names, messages):
        self.users_api = Users(names, messages)

    def users(self):
        return self.users_api


def test_only_exact_label_and_raw_read_no_modify():
    service = Service(
        ["E01", "E01/Projekt-Suchagenten", "E01/Projekt-Suchagenten/Alt"],
        {"id-a": mail("a", 3068473)},
    )
    mailbox = GmailMailbox(GmailSettings(), service)
    items = list(mailbox.iter_messages())

    assert len(items) == 1
    assert items[0].ref == "gmail:id-a"
    assert items[0].raw == mail("a", 3068473)
    assert service.users_api.labels_api.calls == [{"userId": "me"}]
    assert service.users_api.messages_api.list_calls == [
        {"userId": "me", "labelIds": ["Label_1"], "maxResults": 50}
    ]
    assert service.users_api.messages_api.get_calls == [
        {"userId": "me", "id": "id-a", "format": "raw"}
    ]


def test_pages_through_messages_but_obeys_cap():
    messages = {f"id-{i}": mail(str(i), 3068000 + i) for i in range(5)}
    service = Service(["E01/Projekt-Suchagenten"], messages)
    mailbox = GmailMailbox(GmailSettings(max_messages=3), service)
    found = list(mailbox.iter_messages())

    assert len(found) == 3
    assert len(service.users_api.messages_api.get_calls) == 3
    assert service.users_api.messages_api.list_calls[0]["maxResults"] == 3

    # The Gmail API may paginate even when fewer messages than requested returned.
    class SmallPages(Messages):
        def list(self, **kwargs):
            self.list_calls.append(kwargs)
            start = int(kwargs.get("pageToken", "0"))
            ids = list(self.items)[start:start + 1]
            result = {"messages": [{"id": id_} for id_ in ids]}
            if start + 1 < len(self.items):
                result["nextPageToken"] = str(start + 1)
            return Request(result)

    service.users_api.messages_api = SmallPages(messages)
    assert len(list(GmailMailbox(GmailSettings(max_messages=3), service).iter_messages())) == 3
    assert [call.get("pageToken") for call in service.users_api.messages_api.list_calls] == [None, "1", "2"]


def test_unknown_label_fails_closed_and_does_not_read_other_mail():
    service = Service(["INBOX", "E01/Anderes"], {"id-a": mail("a", 3068473)})
    with pytest.raises(SourceError, match="nicht gefunden"):
        list(GmailMailbox(GmailSettings(), service).iter_messages())
    assert service.users_api.messages_api.list_calls == []
    assert service.users_api.messages_api.get_calls == []


def test_missing_or_corrupt_raw_data_fails_closed():
    for raw in ["", "$$$$"]:
        service = Service(["E01/Projekt-Suchagenten"], {"id-a": raw})
        with pytest.raises(SourceError, match="Rohdaten"):
            list(GmailMailbox(GmailSettings(), service).iter_messages())


def test_roundtrip_into_existing_email_connector_and_idempotency():
    service = Service(["E01/Projekt-Suchagenten"], {
        "id-a": mail("a", 3068473),
        "id-b": mail("b", 3068469),
    })
    seen = InMemorySeenMessageStore()
    connector = EmailSourceConnector(EmailSourceSettings(), GmailMailbox(GmailSettings(), service), seen_store=seen)

    first = connector.fetch()
    assert first.ok and [item.source_item_id for item in first.items] == ["3068473", "3068469"]
    assert all(item.source_url.endswith(".html") for item in first.items)
    connector.acknowledge(first)
    second = connector.fetch()
    assert second.ok and second.items == [] and second.stats.duplicates == 2


def test_default_source_remains_offline_and_explicit_gmail_override(monkeypatch):
    assert EmailSourceSettings().backend == "eml"
    assert GmailSettings().label == "E01/Projekt-Suchagenten"
    assert GMAIL_READONLY_SCOPE.endswith("/gmail.readonly")

    monkeypatch.setenv("AGENT_SOURCES__EMAIL__BACKEND", "gmail")
    settings = load_settings(env_file=None)
    assert settings.sources.email.backend == "gmail"


def test_missing_token_requires_explicit_local_authorization(tmp_path, monkeypatch):
    import src.sources.email.gmail_mailbox as module

    monkeypatch.setattr(module, "_require_google_libraries", lambda: None)
    with pytest.raises(SourceError, match="gmail-auth"):
        build_gmail_service(GmailSettings(token_file=tmp_path / "missing.json"))


def test_token_is_private_on_posix(tmp_path):
    token_path = tmp_path / "private" / "gmail-token.json"
    _save_private_token(token_path, '{"test": true}')
    assert token_path.read_text(encoding="utf-8") == '{"test": true}'
    if os.name == "posix":
        assert token_path.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("invalid_max", [0, -1, 5001])
def test_invalid_gmail_limit_rejected(invalid_max):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        GmailSettings(max_messages=invalid_max)
