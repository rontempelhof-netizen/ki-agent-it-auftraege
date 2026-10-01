"""Deterministische Hard-Fail-Regeln (docs/requirements.md, Abschnitt 4).

Eine Regel greift nur bei positiv bekannten Fakten. Fehlende Informationen
(None) lösen niemals einen Hard Fail aus.
"""

from __future__ import annotations

from collections.abc import Callable

from src.config import ScoringSettings
from src.domain.enums import EngagementType, RemoteStatus, ScopeClarity, ScopeSize
from src.domain.models import HardFailReason, LeadAnalysis
from src.scoring.budget import estimate_day_rate_eur

HardFailRule = Callable[[LeadAnalysis, ScoringSettings], HardFailReason | None]


def missing_certifications(analysis: LeadAnalysis, settings: ScoringSettings) -> HardFailReason | None:
    available = {c.casefold() for c in settings.hard_fail.available_certifications}
    missing = [c for c in analysis.required_certifications if c.casefold() not in available]
    if missing:
        return HardFailReason(
            code="missing_certification",
            message=f"Zwingende Zertifizierung nicht vorhanden: {', '.join(missing)}",
        )
    return None


def security_clearance(analysis: LeadAnalysis, settings: ScoringSettings) -> HardFailReason | None:
    if analysis.requires_security_clearance and not settings.hard_fail.has_security_clearance:
        return HardFailReason(code="security_clearance", message="Sicherheitsüberprüfung/-freigabe erforderlich")
    return None


def permanent_employment(analysis: LeadAnalysis, _settings: ScoringSettings) -> HardFailReason | None:
    if analysis.engagement_type == EngagementType.PERMANENT_EMPLOYMENT:
        return HardFailReason(code="permanent_employment", message="Reine Festanstellung, kein Projekt")
    return None


def staff_leasing(analysis: LeadAnalysis, _settings: ScoringSettings) -> HardFailReason | None:
    if analysis.engagement_type == EngagementType.STAFF_LEASING:
        return HardFailReason(code="staff_leasing", message="Faktische Vollzeit-Personalüberlassung")
    return None


def onsite_region(analysis: LeadAnalysis, settings: ScoringSettings) -> HardFailReason | None:
    allowed = settings.hard_fail.onsite_allowed_regions
    if analysis.remote_status == RemoteStatus.ONSITE and analysis.region is not None and analysis.region not in allowed:
        return HardFailReason(
            code="onsite_region",
            message=f"Präsenzpflicht in ungeeigneter Region ({analysis.region})",
        )
    return None


def unrealistic_budget(analysis: LeadAnalysis, settings: ScoringSettings) -> HardFailReason | None:
    day_rate = estimate_day_rate_eur(analysis, settings.budget)
    if day_rate is not None and day_rate < settings.hard_fail.min_day_rate_eur:
        return HardFailReason(
            code="unrealistic_budget",
            message=(
                f"Unrealistisches Budget: ca. {day_rate:.0f} EUR/Tag "
                f"(Untergrenze {settings.hard_fail.min_day_rate_eur:.0f} EUR/Tag)"
            ),
        )
    return None


def scope_too_large(analysis: LeadAnalysis, settings: ScoringSettings) -> HardFailReason | None:
    days = analysis.estimated_person_days
    if days is not None and days > settings.hard_fail.max_person_days:
        return HardFailReason(
            code="scope_too_large",
            message=f"Umfang zu groß: ca. {days:g} PT (max. {settings.hard_fail.max_person_days:g} PT)",
        )
    if analysis.scope_size == ScopeSize.VERY_LARGE:
        return HardFailReason(code="scope_too_large", message="Umfang zu groß für ein kleines Team")
    return None


def too_many_must_haves(analysis: LeadAnalysis, settings: ScoringSettings) -> HardFailReason | None:
    count = len(analysis.must_have_requirements)
    if count > settings.hard_fail.max_must_have_requirements:
        return HardFailReason(
            code="too_many_must_haves",
            message=f"Zu lange Muss-Liste: {count} Anforderungen (max. {settings.hard_fail.max_must_have_requirements})",
        )
    return None


def liability_unclear_scope(analysis: LeadAnalysis, _settings: ScoringSettings) -> HardFailReason | None:
    if analysis.high_liability and analysis.scope_clarity == ScopeClarity.VAGUE:
        return HardFailReason(code="liability_unclear_scope", message="Hohe Haftung bei unklarem Scope")
    return None


HARD_FAIL_RULES: tuple[HardFailRule, ...] = (
    missing_certifications,
    security_clearance,
    permanent_employment,
    staff_leasing,
    onsite_region,
    unrealistic_budget,
    scope_too_large,
    too_many_must_haves,
    liability_unclear_scope,
)


def evaluate_hard_fails(analysis: LeadAnalysis, settings: ScoringSettings) -> list[HardFailReason]:
    """Wendet alle Regeln an und liefert sämtliche zutreffenden Gründe."""
    return [reason for rule in HARD_FAIL_RULES if (reason := rule(analysis, settings)) is not None]
