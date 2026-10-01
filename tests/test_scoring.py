from __future__ import annotations

import pytest

from src.config import ScoringSettings
from src.domain.enums import BudgetType, LeadClass, Level, ScoreCriterion
from src.domain.models import LeadAnalysis
from src.scoring.budget import estimate_day_rate_eur
from src.scoring.engine import ScoreEngine, round_half_up
from tests.fixtures.leads import (
    ALL_CASES,
    ALL_UNKNOWN,
    CSV_IMPORT_79,
    CSV_IMPORT_80,
    LEGACY_PHP_49,
    LEGACY_PHP_50,
    PERFECT_SMALL_PROJECT,
    PROCESS_CONSULTING_64,
    PROCESS_CONSULTING_65,
    SECURITY_CLEARANCE_PUBLIC,
    SHOPWARE_BUGFIX,
    ScoreCase,
)


@pytest.fixture
def engine() -> ScoreEngine:
    return ScoreEngine(ScoringSettings())


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda c: c.name)
def test_known_score_cases_match_hand_calculated_breakdown(engine: ScoreEngine, case: ScoreCase):
    result = engine.score(case.analysis)

    assert {item.criterion.value: item.points for item in result.items} == case.expected_points
    assert result.total == case.expected_total
    assert result.lead_class == case.expected_class
    assert [r.code for r in result.hard_fail_reasons] == case.expected_hard_fail_codes


@pytest.mark.parametrize("case", ALL_CASES, ids=lambda c: c.name)
def test_breakdown_is_complete_and_sums_to_total(engine: ScoreEngine, case: ScoreCase):
    result = engine.score(case.analysis)

    assert [item.criterion for item in result.items] == list(ScoreCriterion)
    assert sum(item.max_points for item in result.items) == 100
    assert sum(item.points for item in result.items) == result.total
    assert all(0 <= item.points <= item.max_points for item in result.items)
    assert all(item.reason for item in result.items)


@pytest.mark.parametrize(
    ("lower", "upper", "lower_class", "upper_class"),
    [
        (LEGACY_PHP_49, LEGACY_PHP_50, LeadClass.REJECT, LeadClass.C),
        (PROCESS_CONSULTING_64, PROCESS_CONSULTING_65, LeadClass.C, LeadClass.B),
        (CSV_IMPORT_79, CSV_IMPORT_80, LeadClass.B, LeadClass.A),
    ],
    ids=["reject_c_49_50", "c_b_64_65", "b_a_79_80"],
)
def test_class_boundaries_with_realistic_leads(engine, lower, upper, lower_class, upper_class):
    low, high = engine.score(lower.analysis), engine.score(upper.analysis)

    assert high.total - low.total == 1
    assert (low.lead_class, high.lead_class) == (lower_class, upper_class)
    changed = [a.criterion for a, b in zip(low.items, high.items, strict=True) if a.points != b.points]
    assert len(changed) == 1, "Grenzfälle sollen sich in genau einem Kriterium unterscheiden"


def test_maximum_score_is_100(engine):
    result = engine.score(PERFECT_SMALL_PROJECT.analysis)

    assert result.total == 100
    assert result.lead_class == LeadClass.A


def test_hard_fail_overrides_high_score(engine):
    analysis = PERFECT_SMALL_PROJECT.analysis.model_copy(update={"required_certifications": ["SAP Certified"]})

    result = engine.score(analysis)

    assert result.total == 100
    assert result.hard_fail
    assert result.lead_class == LeadClass.REJECT


def test_hard_fail_overrides_c_range_score(engine):
    result = engine.score(SECURITY_CLEARANCE_PUBLIC.analysis)

    assert result.total == 52  # ohne Hard Fail wäre das ein C-Lead
    assert result.lead_class == LeadClass.REJECT


def test_missing_information_gets_unknown_fraction_and_is_explained(engine):
    result = engine.score(ALL_UNKNOWN.analysis)

    assert result.total == 40
    assert not result.hard_fail
    assert "unbekannt" in next(i.reason for i in result.items if i.criterion == ScoreCriterion.URGENCY)
    assert "nicht bestimmbar" in next(i.reason for i in result.items if i.criterion == ScoreCriterion.BUDGET_EFFORT)


def test_reasons_document_inputs(engine):
    result = engine.score(SHOPWARE_BUGFIX.analysis)
    reasons = {item.criterion: item.reason for item in result.items}

    assert "5 PT" in reasons[ScoreCriterion.SCOPE]
    assert "800 EUR" in reasons[ScoreCriterion.BUDGET_EFFORT]
    assert "direct_sme" in reasons[ScoreCriterion.DIRECT_CUSTOMER]


def test_unknown_fraction_is_configurable():
    engine = ScoreEngine(ScoringSettings(unknown_fraction=0.0))

    assert engine.score(ALL_UNKNOWN.analysis).total == 0


def test_weights_and_thresholds_are_configurable():
    settings = ScoringSettings.model_validate(
        {
            "weights": {"scope": 30, "deliverability": 15, "win_probability": 10, "budget_effort": 15,
                        "direct_customer": 10, "remote_fit": 5, "urgency": 5, "follow_up": 5, "consulting_fit": 5},
            "thresholds": {"a": 90, "b": 70, "c": 40},
        }
    )
    result = ScoreEngine(settings).score(SHOPWARE_BUGFIX.analysis)

    assert result.points_for(ScoreCriterion.SCOPE) == 30
    assert result.points_for(ScoreCriterion.WIN_PROBABILITY) == 10
    assert result.total == 93
    assert result.lead_class == LeadClass.A


def test_long_must_have_list_reduces_win_probability(engine):
    base = SHOPWARE_BUGFIX.analysis
    long_list = base.model_copy(update={"must_have_requirements": [f"Skill {i}" for i in range(9)]})

    assert engine.score(base).points_for(ScoreCriterion.WIN_PROBABILITY) == 20
    assert engine.score(long_list).points_for(ScoreCriterion.WIN_PROBABILITY) == 15  # (1.0 - 0.25) * 20


@pytest.mark.parametrize(
    ("budget_type", "amount", "days", "expected_points"),
    [
        (BudgetType.DAILY, 800, None, 15),   # ≥ Ziel
        (BudgetType.DAILY, 799, None, 9),    # ≥ akzeptabel
        (BudgetType.DAILY, 600, None, 9),
        (BudgetType.DAILY, 599, None, 3),    # ≥ Untergrenze
        (BudgetType.DAILY, 300, None, 3),
        (BudgetType.DAILY, 299, None, 0),    # unter Untergrenze
        (BudgetType.FIXED, 8000, None, 9),   # Festpreis im Erstauftragsrahmen, Aufwand unbekannt
        (BudgetType.FIXED, 500, None, 3),    # unter Erstauftragsrahmen
        (BudgetType.FIXED, 40000, None, 6),  # über Erstauftragsrahmen
        (None, 5000, None, 6),               # Budgetart unbekannt -> unknown_fraction
    ],
)
def test_budget_effort_thresholds(engine, budget_type, amount, days, expected_points):
    analysis = LeadAnalysis(budget_min=amount, currency="EUR", budget_type=budget_type, estimated_person_days=days)

    assert engine.score(analysis).points_for(ScoreCriterion.BUDGET_EFFORT) == expected_points


def test_budget_without_currency_is_not_assumed_to_be_eur(engine):
    analysis = LeadAnalysis(budget_min=1000, budget_type=BudgetType.DAILY)

    assert estimate_day_rate_eur(analysis, ScoringSettings().budget) is None
    assert engine.score(analysis).points_for(ScoreCriterion.BUDGET_EFFORT) == 6


@pytest.mark.parametrize(
    ("analysis", "expected"),
    [
        (LeadAnalysis(budget_min=100, currency="EUR", budget_type=BudgetType.HOURLY), 800),
        (LeadAnalysis(budget_min=80, budget_max=100, currency="EUR", budget_type=BudgetType.HOURLY), 720),
        (LeadAnalysis(budget_max=700, currency="EUR", budget_type=BudgetType.DAILY), 700),
        (LeadAnalysis(budget_min=6000, currency="EUR", budget_type=BudgetType.FIXED, estimated_person_days=10), 600),
        (LeadAnalysis(budget_min=100, currency="CHF", budget_type=BudgetType.HOURLY), 840),
        (LeadAnalysis(budget_min=6000, currency="EUR", budget_type=BudgetType.FIXED), None),
        (LeadAnalysis(budget_min=100, currency="JPY", budget_type=BudgetType.HOURLY), None),
    ],
)
def test_estimate_day_rate(analysis, expected):
    result = estimate_day_rate_eur(analysis, ScoringSettings().budget)

    assert result == (pytest.approx(expected) if expected is not None else None)


@pytest.mark.parametrize(("value", "expected"), [(2.5, 3), (8.5, 9), (4.3, 4), (4.49, 4), (0.0, 0), (14.999999999, 15)])
def test_round_half_up(value, expected):
    assert round_half_up(value) == expected


def test_scoring_is_deterministic(engine):
    assert engine.score(SHOPWARE_BUGFIX.analysis) == engine.score(SHOPWARE_BUGFIX.analysis)


def test_level_none_vs_low_differs(engine):
    """Unbekannt (None) ist nicht dasselbe wie ein explizit niedriger Wert."""
    unknown = engine.score(LeadAnalysis()).points_for(ScoreCriterion.URGENCY)
    low = engine.score(LeadAnalysis(urgency=Level.LOW)).points_for(ScoreCriterion.URGENCY)

    assert (unknown, low) == (2, 0)
