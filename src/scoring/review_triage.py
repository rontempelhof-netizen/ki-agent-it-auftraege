"""Konservative, nur lesende Sichtung fachlich passender Leads trotz niedrigem Score.

Diese zweite Einstufung ist KEINE Änderung der Score Engine und kein
Vertriebsstatus. Sie wird aus gespeicherten Daten deterministisch abgeleitet.
Unbekannte Angaben bleiben unbekannt; daraus entsteht keine A/B-Einstufung.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.enums import LeadClass, Level, ProcessingStatus
from src.domain.models import Lead

SHORT_DESCRIPTION_CHARS = 250
MIN_UNKNOWN_CRITERIA = 2


@dataclass(frozen=True)
class ReviewAdvice:
    """Manuell zu prüfende fachliche Chance; keine Zusage zur Wirtschaftlichkeit."""

    reasons: tuple[str, ...]
    short_source_text: bool


def review_advice(lead: Lead) -> ReviewAdvice | None:
    """Hebt starke fachliche Fits mit mehreren Informationslücken aus REJECT hervor.

    Keine Aufwertung von Hard-Fails, vorgefilterten/ausstehenden Leads oder
    niedrigen bzw. unbekannten fachlichen Fits. Es wird ausschließlich bereits
    gespeicherte, nach Grounding validierte LLM-Analyse verwendet.
    """
    if (
        lead.processing_status != ProcessingStatus.ANALYZED
        or lead.lead_class != LeadClass.REJECT
        or lead.hard_fail
        or lead.analysis is None
    ):
        return None
    a = lead.analysis
    if a.technical_fit != Level.HIGH and a.consulting_fit != Level.HIGH:
        return None

    gaps: list[str] = []
    if a.budget_min is None and a.budget_max is None:
        gaps.append("Budget oder Tagessatz fehlt")
    if a.estimated_person_days is None and a.scope_size is None:
        gaps.append("Auftragsumfang oder Dauer fehlt")
    if a.customer_type is None:
        gaps.append("Auftraggeber-/Vermittlungsweg ungeklärt")
    if a.entry_barrier is None:
        gaps.append("Zugangshürden ungeklärt")
    if len(gaps) < MIN_UNKNOWN_CRITERIA:
        return None

    short = len(lead.description.strip()) < SHORT_DESCRIPTION_CHARS
    if short:
        gaps.append("Quellbeschreibung kurz – vollständige Ausschreibung prüfen")
    return ReviewAdvice(reasons=tuple(gaps), short_source_text=short)
