"""Normalisierung: RawSourceItem -> LeadCandidate.

Strukturierte Fakten (Budget, Arbeitsmodus) werden nur aus eindeutigen Mustern
übernommen, weil sie in ``merge_source_facts`` Vorrang vor der LLM-Analyse haben.
Alles Mehrdeutige bleibt None und wird der LLM-Analyse überlassen.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime

from src.domain.enums import BudgetType, RemoteStatus
from src.domain.models import LeadCandidate, RawSourceItem
from src.sources.email.extract import html_to_body, normalize_whitespace

MAX_TITLE_LENGTH = 300

_AMOUNT = r"(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?"
_RATE_RE = re.compile(
    rf"{_AMOUNT}(?:\s*[-–]\s*{_AMOUNT})?\s*(?:€|eur(?:o)?)\s*(?:/|pro|je)\s*(h|std\.?|stunde|tag|pt)\b",
    re.IGNORECASE,
)
_FIXED_RE = re.compile(rf"(?:budget|festpreis)\s*:?\s*(?:ca\.\s*)?{_AMOUNT}\s*(?:€|eur(?:o)?)", re.IGNORECASE)
_FIXED_HINT_RE = re.compile(r"festpreis|pauschal", re.IGNORECASE)
_REMOTE_RE = re.compile(r"\bremote\b|\bhomeoffice\b|\bhome office\b", re.IGNORECASE)
_ONSITE_RE = re.compile(r"\bvor ort\b|\bonsite\b|\bon-site\b|\bpräsenz", re.IGNORECASE)
_HYBRID_RE = re.compile(r"\bhybrid\b", re.IGNORECASE)
_FULL_REMOTE_RE = re.compile(r"100\s*%\s*remote|\bfull[- ]remote\b|\bvollständig remote\b", re.IGNORECASE)


class NormalizationError(ValueError):
    pass


def _amount(integer: str, decimals: str | None) -> float:
    return float(integer.replace(".", "") + (f".{decimals}" if decimals else ""))


def extract_budget(text: str) -> tuple[float | None, float | None, str | None, BudgetType | None]:
    """Budget nur aus eindeutigen EUR-Angaben ("80–90 €/h", "650 €/Tag", "Budget: 4.000 € (Festpreis)")."""
    if match := _RATE_RE.search(text):
        low = _amount(match.group(1), match.group(2))
        high = _amount(match.group(3), match.group(4)) if match.group(3) else low
        unit = match.group(5).lower()
        budget_type = BudgetType.HOURLY if unit.startswith(("h", "std", "stunde")) else BudgetType.DAILY
        return min(low, high), max(low, high), "EUR", budget_type
    if (match := _FIXED_RE.search(text)) and _FIXED_HINT_RE.search(text[match.start() : match.end() + 40]):
        value = _amount(match.group(1), match.group(2))
        return value, value, "EUR", BudgetType.FIXED
    return None, None, None, None


def extract_remote_status(text: str) -> RemoteStatus | None:
    if _HYBRID_RE.search(text):
        return RemoteStatus.HYBRID
    remote, onsite = bool(_REMOTE_RE.search(text)), bool(_ONSITE_RE.search(text))
    if remote and onsite:
        return RemoteStatus.REMOTE if _FULL_REMOTE_RE.search(text) else RemoteStatus.HYBRID
    if remote:
        return RemoteStatus.REMOTE
    if onsite:
        return RemoteStatus.ONSITE
    return None


def _source_id(item: RawSourceItem, description: str) -> str:
    if item.source_item_id:
        return item.source_item_id
    basis = item.source_url or f"{item.title or ''}\n{description}"
    return "h-" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


def normalize(item: RawSourceItem, seen_at: datetime) -> LeadCandidate:
    """Wandelt einen Rohdatensatz in einen Kandidaten. ``seen_at`` = Zeitpunkt der ersten Sichtung."""
    if item.body_text:
        description = normalize_whitespace(item.body_text)
    elif item.body_html:
        description = html_to_body(item.body_html).text
    else:
        description = ""
    title = normalize_whitespace(item.title or "").replace("\n", " ")
    if not title:
        title = next((line for line in description.split("\n") if line.strip()), "")
    title = title[:MAX_TITLE_LENGTH].strip()
    if not title:
        raise NormalizationError(f"{item.source}/{item.source_item_id}: weder Titel noch Text vorhanden")

    budget_min, budget_max, currency, budget_type = extract_budget(description)
    return LeadCandidate(
        source=item.source,
        source_id=_source_id(item, description),
        source_url=item.source_url,
        title=title,
        description=description,
        published_at=None,  # Mail-Datum ist kein Veröffentlichungsdatum des Projekts
        first_seen_at=seen_at,
        remote_status=extract_remote_status(f"{title}\n{description}"),
        budget_min=budget_min,
        budget_max=budget_max,
        currency=currency,
        budget_type=budget_type,
    )
