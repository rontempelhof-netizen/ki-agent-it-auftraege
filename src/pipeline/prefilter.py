"""Deterministischer Prefilter vor der LLM-Analyse.

Nur Ausschlussgründe, die ohne LLM zuverlässig erkennbar sind:

1. Reine Festanstellung (z. B. "Festanstellung", "unbefristete Anstellung").
2. Arbeitnehmerüberlassung (ANÜ) – faktische Personalüberlassung.
3. Ausdrücklich angegebener Stunden-/Tagessatz unter der Untergrenze
   (``scoring.hard_fail.min_day_rate_eur``), sofern der Satz eindeutig in EUR extrahiert wurde.
4. Frei konfigurierbare Ausschlussmuster (``prefilter.exclude_patterns``).

Treffer werden ignoriert, wenn kurz davor eine Verneinung steht ("keine Festanstellung").

Bewusst NICHT im Prefilter (erfordern Kontextverständnis, daher LLM + Hard-Fail-Regeln):
Pflichtzertifizierungen und Sicherheitsüberprüfungen (Muss vs. "von Vorteil"), Projektumfang,
Präsenzpflicht/Region, Haftung, Länge der Muss-Liste, Festpreisbudgets ohne Aufwandsangabe.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from pydantic import BaseModel

from src.config import PrefilterSettings, ScoringSettings
from src.domain.models import LeadAnalysis, LeadCandidate
from src.scoring.budget import estimate_day_rate_eur

_NEGATION_RE = re.compile(r"\b(?:kein|keine|keinen|keiner|ohne|nicht|no|not|non)\b", re.IGNORECASE)


class PrefilterResult(BaseModel):
    rejected: bool
    reasons: list[str] = []


def _first_unnegated(patterns: Iterable[re.Pattern[str]], text: str, window: int) -> str | None:
    for pattern in patterns:
        for match in pattern.finditer(text):
            before = text[max(0, match.start() - window) : match.start()]
            if not _NEGATION_RE.search(before):
                return match.group(0)
    return None


class Prefilter:
    def __init__(self, settings: PrefilterSettings, scoring: ScoringSettings) -> None:
        self._settings = settings
        self._scoring = scoring

    def check(self, candidate: LeadCandidate) -> PrefilterResult:
        if not self._settings.enabled:
            return PrefilterResult(rejected=False)
        text = f"{candidate.title}\n{candidate.description}"
        window = self._settings.negation_window
        reasons: list[str] = []

        if hit := _first_unnegated(self._settings.permanent_employment_patterns, text, window):
            reasons.append(f"Prefilter: reine Festanstellung (\"{hit}\")")
        if hit := _first_unnegated(self._settings.staff_leasing_patterns, text, window):
            reasons.append(f"Prefilter: Arbeitnehmerüberlassung (\"{hit}\")")
        if hit := _first_unnegated(self._settings.exclude_patterns, text, window):
            reasons.append(f"Prefilter: Ausschlussmuster (\"{hit}\")")

        rate = estimate_day_rate_eur(
            LeadAnalysis(
                budget_min=candidate.budget_min,
                budget_max=candidate.budget_max,
                currency=candidate.currency,
                budget_type=candidate.budget_type,
            ),
            self._scoring.budget,
        )
        floor = self._scoring.hard_fail.min_day_rate_eur
        if rate is not None and rate < floor:
            reasons.append(f"Prefilter: Satz ca. {rate:.0f} EUR/Tag unter Untergrenze {floor:.0f} EUR/Tag")

        return PrefilterResult(rejected=bool(reasons), reasons=reasons)
