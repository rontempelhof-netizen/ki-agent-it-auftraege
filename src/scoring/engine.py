"""Deterministische Score Engine (0–100).

Jedes Kriterium liefert einen Erfüllungsgrad zwischen 0 und 1. Daraus werden die
Punkte berechnet: ``round_half_up(max_points * Erfüllungsgrad)``. Der Gesamtscore
ist die Summe der ganzzahligen Punkte, die Aufschlüsselung ergibt also immer genau
den Gesamtwert. Unbekannte Merkmale (None) erhalten ``unknown_fraction`` der Punkte.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from enum import StrEnum

from src.config import ScoringSettings
from src.domain.enums import (
    BudgetType,
    CustomerType,
    Level,
    Region,
    RemoteStatus,
    ScopeClarity,
    ScopeSize,
    ScoreCriterion,
)
from src.domain.models import LeadAnalysis, ScoreItem, ScoreResult
from src.scoring.budget import budget_eur, estimate_day_rate_eur
from src.scoring.classification import classify
from src.scoring.hard_fail import evaluate_hard_fails

Fraction = tuple[float, str]
"""Erfüllungsgrad (0–1) und Begründung."""

LEVEL_POSITIVE: Mapping[Level, float] = {Level.HIGH: 1.0, Level.MEDIUM: 0.5, Level.LOW: 0.0}
LEVEL_INVERSE: Mapping[Level, float] = {Level.LOW: 1.0, Level.MEDIUM: 0.5, Level.HIGH: 0.0}
SCOPE_SIZE: Mapping[ScopeSize, float] = {
    ScopeSize.SMALL: 1.0,
    ScopeSize.MEDIUM: 0.5,
    ScopeSize.LARGE: 0.0,
    ScopeSize.VERY_LARGE: 0.0,
}
SCOPE_CLARITY: Mapping[ScopeClarity, float] = {
    ScopeClarity.CLEAR: 1.0,
    ScopeClarity.PARTIAL: 0.5,
    ScopeClarity.VAGUE: 0.0,
}
CUSTOMER_TYPE: Mapping[CustomerType, float] = {
    CustomerType.DIRECT_SME: 1.0,
    CustomerType.DIRECT_ENTERPRISE: 0.6,
    CustomerType.AGENCY: 0.4,
    CustomerType.RECRUITER: 0.3,
    CustomerType.PUBLIC_SECTOR: 0.2,
}
# Remote-Fit je Arbeitsmodus: Region DACH / Region unbekannt / andere Region
REMOTE_FIT: Mapping[RemoteStatus, tuple[float, float, float]] = {
    RemoteStatus.REMOTE: (1.0, 1.0, 1.0),
    RemoteStatus.HYBRID: (0.8, 0.5, 0.2),
    RemoteStatus.ONSITE: (0.6, 0.3, 0.0),
}
CUSTOMER_TYPE_SHARE = 0.7
DECISION_SHARE = 0.3
MUST_HAVE_PENALTY = 0.25


def round_half_up(value: float) -> int:
    # Kleiner Epsilon-Wert gleicht Gleitkommafehler aus (z. B. 0.75 * 20 = 14.999…).
    return math.floor(value + 0.5 + 1e-9)


def _fmt(value: float) -> str:
    return f"{value:g}"


class ScoreEngine:
    def __init__(self, settings: ScoringSettings) -> None:
        self._settings = settings
        self._criteria: dict[ScoreCriterion, Callable[[LeadAnalysis], Fraction]] = {
            ScoreCriterion.SCOPE: self._scope,
            ScoreCriterion.DELIVERABILITY: self._deliverability,
            ScoreCriterion.WIN_PROBABILITY: self._win_probability,
            ScoreCriterion.BUDGET_EFFORT: self._budget_effort,
            ScoreCriterion.DIRECT_CUSTOMER: self._direct_customer,
            ScoreCriterion.REMOTE_FIT: self._remote_fit,
            ScoreCriterion.URGENCY: self._urgency,
            ScoreCriterion.FOLLOW_UP: self._follow_up,
            ScoreCriterion.CONSULTING_FIT: self._consulting_fit,
        }

    def score(self, analysis: LeadAnalysis) -> ScoreResult:
        items = [self._score_item(criterion, rule(analysis)) for criterion, rule in self._criteria.items()]
        hard_fails = evaluate_hard_fails(analysis, self._settings)
        total = sum(item.points for item in items)
        return ScoreResult(
            items=items,
            hard_fail_reasons=hard_fails,
            lead_class=classify(total, self._settings.thresholds, hard_fail=bool(hard_fails)),
        )

    def _score_item(self, criterion: ScoreCriterion, result: Fraction) -> ScoreItem:
        fraction, reason = result
        max_points = getattr(self._settings.weights, criterion.value)
        fraction = min(max(fraction, 0.0), 1.0)
        return ScoreItem(
            criterion=criterion,
            points=round_half_up(max_points * fraction),
            max_points=max_points,
            reason=reason,
        )

    # --- Hilfsfunktionen ---------------------------------------------------

    def _lookup[E: StrEnum](self, value: E | None, mapping: Mapping[E, float], label: str) -> Fraction:
        if value is None:
            return self._settings.unknown_fraction, f"{label} unbekannt"
        return mapping[value], f"{label}: {value}"

    def _combine(self, *parts: tuple[float, Fraction]) -> Fraction:
        fraction = sum(weight * part[0] for weight, part in parts)
        return fraction, "; ".join(part[1] for _, part in parts)

    # --- Kriterien ---------------------------------------------------------

    def _scope(self, a: LeadAnalysis) -> Fraction:
        return self._combine((0.5, self._scope_size(a)), (0.5, self._lookup(a.scope_clarity, SCOPE_CLARITY, "Klarheit")))

    def _scope_size(self, a: LeadAnalysis) -> Fraction:
        cfg = self._settings.scope
        days = a.estimated_person_days
        if days is None:
            return self._lookup(a.scope_size, SCOPE_SIZE, "Umfang")
        if days <= cfg.target_person_days_max:
            return 1.0, f"Umfang ca. {_fmt(days)} PT (Ziel ≤ {_fmt(cfg.target_person_days_max)} PT)"
        if days <= cfg.stretch_person_days_max:
            return 0.5, f"Umfang ca. {_fmt(days)} PT (über Ziel, ≤ {_fmt(cfg.stretch_person_days_max)} PT)"
        return 0.0, f"Umfang ca. {_fmt(days)} PT (> {_fmt(cfg.stretch_person_days_max)} PT)"

    def _deliverability(self, a: LeadAnalysis) -> Fraction:
        return self._lookup(a.technical_fit, LEVEL_POSITIVE, "Technischer Fit")

    def _win_probability(self, a: LeadAnalysis) -> Fraction:
        fraction, reason = self._lookup(a.entry_barrier, LEVEL_INVERSE, "Zugangshürden")
        count = len(a.must_have_requirements)
        limit = self._settings.scope.must_have_soft_limit
        if count > limit:
            return fraction - MUST_HAVE_PENALTY, f"{reason}; {count} Muss-Anforderungen (> {limit}, Abzug)"
        return fraction, reason

    def _budget_effort(self, a: LeadAnalysis) -> Fraction:
        cfg = self._settings.budget
        day_rate = estimate_day_rate_eur(a, cfg)
        if day_rate is not None:
            text = f"Tagessatz ca. {day_rate:.0f} EUR"
            if day_rate >= cfg.target_day_rate_eur:
                return 1.0, f"{text} (≥ Ziel {cfg.target_day_rate_eur:.0f})"
            if day_rate >= cfg.acceptable_day_rate_eur:
                return 0.6, f"{text} (≥ akzeptabel {cfg.acceptable_day_rate_eur:.0f})"
            if day_rate >= self._settings.hard_fail.min_day_rate_eur:
                return 0.2, f"{text} (unter akzeptabel {cfg.acceptable_day_rate_eur:.0f})"
            return 0.0, f"{text} (unter Untergrenze {self._settings.hard_fail.min_day_rate_eur:.0f})"

        amount = budget_eur(a, cfg)
        if amount is not None and a.budget_type == BudgetType.FIXED:
            text = f"Festpreis ca. {amount:.0f} EUR, Aufwand unbekannt"
            if amount < cfg.first_order_min_eur:
                return 0.2, f"{text} (unter {cfg.first_order_min_eur:.0f} EUR)"
            if amount > cfg.first_order_max_eur:
                return 0.4, f"{text} (über Erstauftragsrahmen {cfg.first_order_max_eur:.0f} EUR)"
            return 0.6, f"{text} (im Erstauftragsrahmen)"
        return self._settings.unknown_fraction, "Budget-/Aufwandsverhältnis nicht bestimmbar"

    def _direct_customer(self, a: LeadAnalysis) -> Fraction:
        return self._combine(
            (CUSTOMER_TYPE_SHARE, self._lookup(a.customer_type, CUSTOMER_TYPE, "Kundenart")),
            (DECISION_SHARE, self._lookup(a.decision_complexity, LEVEL_INVERSE, "Entscheidungskomplexität")),
        )

    def _remote_fit(self, a: LeadAnalysis) -> Fraction:
        if a.remote_status is None:
            return self._settings.unknown_fraction, "Arbeitsmodus unbekannt"
        dach, unknown, other = REMOTE_FIT[a.remote_status]
        if a.remote_status == RemoteStatus.REMOTE:
            return dach, "Remote"
        if a.region is None:
            return unknown, f"{a.remote_status}, Region unbekannt"
        return (dach if a.region == Region.DACH else other), f"{a.remote_status}, Region {a.region}"

    def _urgency(self, a: LeadAnalysis) -> Fraction:
        return self._lookup(a.urgency, LEVEL_POSITIVE, "Dringlichkeit")

    def _follow_up(self, a: LeadAnalysis) -> Fraction:
        return self._lookup(a.follow_up_potential, LEVEL_POSITIVE, "Folgepotenzial")

    def _consulting_fit(self, a: LeadAnalysis) -> Fraction:
        return self._lookup(a.consulting_fit, LEVEL_POSITIVE, "Beratungsfit")
