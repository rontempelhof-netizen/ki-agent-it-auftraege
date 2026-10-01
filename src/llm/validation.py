"""Strikte Validierung der LLM-Antwort und Prüfung gegen erfundene Fakten.

1. ``parse_analysis_output``: JSON-Objekt, keine Entscheidungsfelder (Score, Klasse, ...),
   strikte Pydantic-Validierung gegen ``LeadAnalysis`` (keine Zusatzfelder, keine Typ-Coercion).
2. ``ground_analysis``: Faktenfelder, die sich nicht im Quelltext belegen lassen (Budget,
   Währung, Zertifizierungen, Aufwand, Sicherheitsüberprüfung), werden auf None/[] gesetzt.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from src.domain.models import LeadAnalysis

# Felder, mit denen ein LLM eine Bewertung/Entscheidung vorwegnehmen würde.
FORBIDDEN_OUTPUT_FIELDS = frozenset(
    {"score", "score_total", "total_score", "lead_score", "score_breakdown", "classification",
     "class", "lead_class", "rating", "grade", "hard_fail", "hard_fail_reasons", "decision",
     "verdict", "priority", "status", "accept", "reject"}
)
_FORBIDDEN_SUBSTRINGS = ("score", "classif")

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*)\n```$", re.DOTALL)


class OutputValidationError(Exception):
    """Strukturell ungültige Antwort. ``kind``: invalid_json | not_object | forbidden_fields | schema."""

    def __init__(self, kind: str, summary: str) -> None:
        super().__init__(f"{kind}: {summary}")
        self.kind = kind
        self.summary = summary


def _forbidden_keys(data: dict[str, Any]) -> list[str]:
    return sorted(
        key for key in data
        if key.lower() in FORBIDDEN_OUTPUT_FIELDS or any(s in key.lower() for s in _FORBIDDEN_SUBSTRINGS)
    )


def _schema_summary(exc: ValidationError) -> str:
    """Fehlerzusammenfassung ohne Eingabewerte (kein Echo von Inhalten in die Wiederholung)."""
    parts = [f"{'.'.join(str(p) for p in err['loc']) or 'root'}: {err['type']}" for err in exc.errors()[:6]]
    return "; ".join(parts)


def parse_analysis_output(text: str) -> LeadAnalysis:
    stripped = text.strip()
    if match := _FENCE_RE.match(stripped):
        stripped = match.group(1).strip()
    if not stripped:
        raise OutputValidationError("invalid_json", "leere Antwort")
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise OutputValidationError("invalid_json", f"kein gültiges JSON (Zeile {exc.lineno}, Spalte {exc.colno})") from exc
    if not isinstance(data, dict):
        raise OutputValidationError("not_object", f"JSON-Objekt erwartet, erhalten: {type(data).__name__}")
    if forbidden := _forbidden_keys(data):
        raise OutputValidationError("forbidden_fields", f"unzulässige Entscheidungsfelder: {', '.join(forbidden)}")
    try:
        return LeadAnalysis.model_validate_json(stripped, strict=True)
    except ValidationError as exc:
        raise OutputValidationError("schema", _schema_summary(exc)) from exc


# --- Grounding ------------------------------------------------------------------

_NUMBER_RE = re.compile(r"\d+(?:[.,'’]\d+)*")
_THOUSANDS_SUFFIX_RE = re.compile(r"\s?(?:k|tsd\.?|tausend|t€)\b", re.IGNORECASE)
_CURRENCY_HINTS: dict[str, tuple[str, ...]] = {
    "EUR": ("€", "eur", "euro"),
    "USD": ("$", "usd", "dollar"),
    "CHF": ("chf", "franken", "sfr"),
    "GBP": ("£", "gbp", "pfund", "pound"),
}
_NUMBER_WORDS = r"(?:ein|eine|einen|zwei|drei|vier|fünf|sechs|sieben|acht|neun|zehn|elf|zwölf|halbe?n?|one|two|three|four|five|six|a|an)"
_EFFORT_RE = re.compile(
    rf"(?:\d+(?:[.,]\d+)?|{_NUMBER_WORDS})\s*[-–]?\s*"
    r"(?:pt\b|personentag|manntag|mt\b|arbeitstag|tag|woche|monat|jahr|stunde|std\b|h\b|day|week|month|hour|sprint)",
    re.IGNORECASE,
)
_CLEARANCE_RE = re.compile(
    r"sicherheitsüberprüfung|sicherheitsfreigabe|\bü\s?[123]\b|\bsü\s?[123]?\b|security clearance|"
    r"\bclearance\b|geheimschutz|vs-nfd",
    re.IGNORECASE,
)
_CERT_GENERIC_TOKENS = frozenset(
    {"zertifikat", "zertifizierung", "zertifiziert", "certified", "certification", "certificate",
     "level", "nachweis", "foundation", "advanced", "professional", "der", "die", "das", "und", "and", "of"}
)


def number_values(text: str) -> set[float]:
    """Alle plausiblen Zahlenwerte im Text (deutsche und englische Schreibweisen, 5k = 5000)."""
    values: set[float] = set()
    for match in _NUMBER_RE.finditer(text):
        token = match.group(0).strip()
        candidates = {re.sub(r"[.,'’]", "", token)}
        if re.search(r"[.,]\d{1,2}$", token):  # letzte Gruppe als Dezimalstellen
            integer, decimals = re.split(r"[.,](?=\d{1,2}$)", token)
            candidates.add(f"{re.sub(r'[.,’]', '', integer)}.{decimals}")
        multiplier = 1000 if _THOUSANDS_SUFFIX_RE.match(text, match.end()) else 1
        for candidate in candidates:
            try:
                value = float(candidate)
            except ValueError:
                continue
            values.add(value)
            values.add(value * multiplier)
    return values


def _value_grounded(value: float, numbers: set[float]) -> bool:
    return any(abs(value - n) <= max(0.01, abs(n) * 0.005) for n in numbers)


def _currency_grounded(currency: str, text: str) -> bool:
    lowered = text.lower()
    hints = _CURRENCY_HINTS.get(currency.upper(), (currency.lower(),))
    return any(hint in lowered for hint in hints)


def _certification_grounded(certification: str, text: str) -> bool:
    lowered = text.casefold()
    if certification.casefold() in lowered:
        return True
    tokens = [t for t in re.findall(r"[\wäöüß+-]{3,}", certification.casefold()) if t not in _CERT_GENERIC_TOKENS]
    return any(token in lowered for token in tokens)


def ground_analysis(analysis: LeadAnalysis, source_text: str) -> tuple[LeadAnalysis, list[str]]:
    """Verwirft nicht belegbare Fakten (setzt sie auf None/[]) und meldet sie."""
    updates: dict[str, Any] = {}
    issues: list[str] = []
    numbers = number_values(source_text)

    budget_values = {f: getattr(analysis, f) for f in ("budget_min", "budget_max") if getattr(analysis, f) is not None}
    ungrounded = {f: v for f, v in budget_values.items() if not _value_grounded(v, numbers)}
    for field, value in ungrounded.items():
        updates[field] = None
        issues.append(f"{field}={value:g} nicht im Quelltext belegt, verworfen")
    if budget_values and len(ungrounded) == len(budget_values):
        updates |= {"currency": None, "budget_type": None}
    elif analysis.currency and not _currency_grounded(analysis.currency, source_text):
        updates["currency"] = None
        issues.append(f"currency={analysis.currency} nicht im Quelltext belegt, verworfen")

    kept = [c for c in analysis.required_certifications if _certification_grounded(c, source_text)]
    for certification in analysis.required_certifications:
        if certification not in kept:
            issues.append(f"Zertifizierung '{certification}' nicht im Quelltext belegt, verworfen")
    if len(kept) != len(analysis.required_certifications):
        updates["required_certifications"] = kept

    if analysis.estimated_person_days is not None and not _EFFORT_RE.search(source_text):
        updates["estimated_person_days"] = None
        issues.append(f"estimated_person_days={analysis.estimated_person_days:g} ohne Umfangsangabe im Quelltext, verworfen")

    if analysis.requires_security_clearance and not _CLEARANCE_RE.search(source_text):
        updates["requires_security_clearance"] = None
        issues.append("Sicherheitsüberprüfung nicht im Quelltext belegt, verworfen")

    return (analysis.model_copy(update=updates) if updates else analysis), issues
