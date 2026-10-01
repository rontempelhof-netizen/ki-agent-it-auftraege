from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from src.sources.email.parser import parse_email
from tests.email_fixtures import (
    BROKEN_GARBAGE,
    BROKEN_INCOMPLETE,
    BROKEN_MALFORMED,
    FREELANCE_DE_HTML,
    FREELANCE_DE_TEXT,
    FREELANCERMAP_MULTI,
    FREELANCERMAP_REDELIVERED,
    load,
)

CEST = timezone(timedelta(hours=2))


def test_freelancermap_multipart_headers_and_bodies():
    parsed = parse_email(load(FREELANCERMAP_MULTI))

    assert parsed.sender_address == "projektagent@freelancermap.de"
    assert parsed.sender_name == "freelancermap Projekt-Agent"
    assert parsed.subject == "3 neue Projekte für Ihren Suchagenten „Python & Shop Remote“"
    assert parsed.date == datetime(2026, 9, 30, 7, 2, 11, tzinfo=CEST)
    assert parsed.message_id == "20260930070211.4f2a1c@mailer.freelancermap.de"
    assert parsed.key == "mid:20260930070211.4f2a1c@mailer.freelancermap.de"
    assert "Shopware 6: Fehler im Checkout" in parsed.text_body
    assert "<html" in parsed.html_body and "Unterstützung" in parsed.html_body
    assert parsed.problems == []


def test_quoted_printable_text_with_umlauts():
    parsed = parse_email(load(FREELANCE_DE_TEXT))

    assert parsed.html_body is None
    assert "mittelständischen Großhändler" in parsed.text_body
    assert "projekt-1187245-Python-Entwickler-m-w-d-fuer-API-Anbindung-Warenwirtschaft" in parsed.text_body


def test_latin1_html_only():
    parsed = parse_email(load(FREELANCE_DE_HTML))

    assert parsed.text_body is None
    assert "Messgeräten" in parsed.html_body


def test_redelivered_mail_has_same_key():
    assert parse_email(load(FREELANCERMAP_MULTI)).key == parse_email(load(FREELANCERMAP_REDELIVERED)).key


def test_incomplete_mail_without_headers_and_body():
    parsed = parse_email(load(BROKEN_INCOMPLETE))

    assert parsed.sender_address is None and parsed.date is None and parsed.message_id is None
    assert parsed.subject == "WG: Projektanfrage"
    assert not parsed.has_content
    assert parsed.key.startswith("sha256:")
    assert "Absender fehlt oder ist ungültig" in parsed.problems
    assert "Date-Header fehlt" in parsed.problems


def test_malformed_mail_is_parsed_as_far_as_possible():
    parsed = parse_email(load(BROKEN_MALFORMED))

    assert parsed.sender_address == "projektagent@freelancermap.de"
    assert parsed.subject == "1 neues Projekt für Ihren Suchagenten"
    assert parsed.date is None
    assert "REST-API für Ticketsystem" in parsed.html_body
    assert "REST-API f" in parsed.text_body  # unbekannter Zeichensatz -> Ersatzdekodierung
    assert any("Date-Header ungültig" in p for p in parsed.problems)
    assert any("CloseBoundaryNotFoundDefect" in p for p in parsed.problems)
    assert any("Ersatzdekodierung" in p for p in parsed.problems)


def test_binary_garbage_does_not_raise():
    parsed = parse_email(load(BROKEN_GARBAGE))

    assert not parsed.has_headers


def test_date_without_timezone_interpreted_as_utc():
    raw = b"From: a@freelance.de\r\nDate: Wed, 30 Sep 2026 06:30:00 -0000\r\nSubject: x\r\n\r\nText\r\n"

    parsed = parse_email(raw)

    assert parsed.date == datetime(2026, 9, 30, 6, 30, tzinfo=UTC)
    assert any("ohne Zeitzone" in p for p in parsed.problems)


def test_attachments_are_ignored():
    raw = (
        b"From: a@freelance.de\r\nSubject: x\r\nMIME-Version: 1.0\r\n"
        b'Content-Type: multipart/mixed; boundary="b"\r\n\r\n'
        b"--b\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nHaupttext\r\n"
        b'--b\r\nContent-Type: text/plain\r\nContent-Disposition: attachment; filename="agb.txt"\r\n\r\nAGB\r\n'
        b"--b--\r\n"
    )

    assert parse_email(raw).text_body.strip() == "Haupttext"


def test_key_without_message_id_is_content_based():
    raw = b"From: a@freelance.de\r\nSubject: x\r\n\r\nText\r\n"
    other = b"Received: by mx2\r\nFrom: a@freelance.de\r\nSubject: x\r\n\r\nText\r\n"

    assert parse_email(raw).key == parse_email(other).key
    assert parse_email(raw).key != parse_email(raw.replace(b"Text", b"Anderer Text")).key
