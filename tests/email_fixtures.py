"""Hilfen für die .eml-Fixtures in tests/fixtures/emails."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

EMAIL_DIR = Path(__file__).parent / "fixtures" / "emails"
FIXED_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

FREELANCERMAP_MULTI = "freelancermap_multi.eml"
FREELANCERMAP_REDELIVERED = "freelancermap_multi_redelivered.eml"
FREELANCE_DE_TEXT = "freelance_de_text.eml"
FREELANCE_DE_HTML = "freelance_de_html_latin1.eml"
UNKNOWN_SENDER = "unknown_sender_newsletter.eml"
BROKEN_INCOMPLETE = "broken_incomplete_no_headers.eml"
BROKEN_MALFORMED = "broken_malformed_freelancermap.eml"
BROKEN_GARBAGE = "broken_binary_garbage.eml"


def load(name: str) -> bytes:
    return (EMAIL_DIR / name).read_bytes()


def fixed_clock() -> datetime:
    return FIXED_NOW
