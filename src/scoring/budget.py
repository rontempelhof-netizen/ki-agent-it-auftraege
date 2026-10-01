"""Budget-Hilfsfunktionen: Umrechnung in EUR und impliziter Tagessatz."""

from __future__ import annotations

from src.config import BudgetSettings
from src.domain.enums import BudgetType
from src.domain.models import LeadAnalysis


def budget_reference(analysis: LeadAnalysis) -> float | None:
    """Mittelwert aus budget_min/budget_max bzw. der eine bekannte Wert."""
    values = [v for v in (analysis.budget_min, analysis.budget_max) if v is not None]
    return sum(values) / len(values) if values else None


def to_eur(amount: float, currency: str | None, settings: BudgetSettings) -> float | None:
    """Rechnet in EUR um. Unbekannte oder nicht konfigurierte Währung -> None."""
    if currency is None:
        return None
    rate = settings.currency_rates_to_eur.get(currency.upper())
    return amount * rate if rate is not None else None


def budget_eur(analysis: LeadAnalysis, settings: BudgetSettings) -> float | None:
    amount = budget_reference(analysis)
    return None if amount is None else to_eur(amount, analysis.currency, settings)


def estimate_day_rate_eur(analysis: LeadAnalysis, settings: BudgetSettings) -> float | None:
    """Impliziter Tagessatz in EUR oder None, wenn dafür Angaben fehlen."""
    amount = budget_eur(analysis, settings)
    if amount is None:
        return None
    match analysis.budget_type:
        case BudgetType.HOURLY:
            return amount * settings.hours_per_day
        case BudgetType.DAILY:
            return amount
        case BudgetType.FIXED if analysis.estimated_person_days:
            return amount / analysis.estimated_person_days
        case _:
            return None
