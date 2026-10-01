"""A/B/C/REJECT-Klassifizierung (docs/requirements.md, Abschnitt 8)."""

from __future__ import annotations

from src.config import ClassThresholds
from src.domain.enums import LeadClass


def classify(total: int, thresholds: ClassThresholds, hard_fail: bool = False) -> LeadClass:
    """Ordnet einen Gesamtscore einer Klasse zu. Ein Hard Fail übersteuert den Score."""
    if not 0 <= total <= 100:
        raise ValueError(f"Score muss zwischen 0 und 100 liegen, ist {total}")
    if hard_fail:
        return LeadClass.REJECT
    if total >= thresholds.a:
        return LeadClass.A
    if total >= thresholds.b:
        return LeadClass.B
    if total >= thresholds.c:
        return LeadClass.C
    return LeadClass.REJECT
