from __future__ import annotations

import pytest

from src.config import HardFailSettings, ScoringSettings
from src.domain.enums import BudgetType, EngagementType, Region, RemoteStatus, ScopeClarity, ScopeSize
from src.domain.models import LeadAnalysis
from src.scoring.hard_fail import evaluate_hard_fails
from tests.fixtures.leads import ALL_CASES, ALL_UNKNOWN, SHOPWARE_BUGFIX

SETTINGS = ScoringSettings()


def codes(analysis: LeadAnalysis, settings: ScoringSettings = SETTINGS) -> list[str]:
    return [r.code for r in evaluate_hard_fails(analysis, settings)]


def test_good_lead_has_no_hard_fail():
    assert codes(SHOPWARE_BUGFIX.analysis) == []


def test_unknown_information_never_triggers_hard_fail():
    assert codes(ALL_UNKNOWN.analysis) == []
    assert codes(LeadAnalysis()) == []


@pytest.mark.parametrize(
    ("analysis", "expected_code"),
    [
        (LeadAnalysis(required_certifications=["AWS Solutions Architect Professional"]), "missing_certification"),
        (LeadAnalysis(requires_security_clearance=True), "security_clearance"),
        (LeadAnalysis(engagement_type=EngagementType.PERMANENT_EMPLOYMENT), "permanent_employment"),
        (LeadAnalysis(engagement_type=EngagementType.STAFF_LEASING), "staff_leasing"),
        (LeadAnalysis(remote_status=RemoteStatus.ONSITE, region=Region.INTERNATIONAL), "onsite_region"),
        (LeadAnalysis(budget_min=35, currency="EUR", budget_type=BudgetType.HOURLY), "unrealistic_budget"),
        (LeadAnalysis(estimated_person_days=61), "scope_too_large"),
        (LeadAnalysis(scope_size=ScopeSize.VERY_LARGE), "scope_too_large"),
        (LeadAnalysis(must_have_requirements=[f"Req {i}" for i in range(16)]), "too_many_must_haves"),
        (LeadAnalysis(high_liability=True, scope_clarity=ScopeClarity.VAGUE), "liability_unclear_scope"),
    ],
)
def test_each_rule_triggers(analysis, expected_code):
    assert codes(analysis) == [expected_code]


@pytest.mark.parametrize(
    "analysis",
    [
        LeadAnalysis(requires_security_clearance=False),
        LeadAnalysis(engagement_type=EngagementType.PROJECT),
        LeadAnalysis(remote_status=RemoteStatus.ONSITE, region=Region.DACH),
        LeadAnalysis(remote_status=RemoteStatus.ONSITE),  # Region unbekannt -> keine Annahme
        LeadAnalysis(remote_status=RemoteStatus.HYBRID, region=Region.INTERNATIONAL),
        LeadAnalysis(budget_min=300, currency="EUR", budget_type=BudgetType.DAILY),  # genau Untergrenze
        LeadAnalysis(budget_min=35, budget_type=BudgetType.HOURLY),  # Währung unbekannt
        LeadAnalysis(estimated_person_days=60),  # genau Maximum
        LeadAnalysis(scope_size=ScopeSize.LARGE),
        LeadAnalysis(must_have_requirements=[f"Req {i}" for i in range(15)]),  # genau Maximum
        LeadAnalysis(high_liability=True, scope_clarity=ScopeClarity.CLEAR),
        LeadAnalysis(high_liability=True),
    ],
)
def test_rules_do_not_trigger_at_or_below_limits(analysis):
    assert codes(analysis) == []


def test_available_certification_is_accepted_case_insensitively():
    settings = ScoringSettings(hard_fail=HardFailSettings(available_certifications=["ISTQB Foundation"]))
    analysis = LeadAnalysis(required_certifications=["istqb foundation", "PRINCE2"])

    reasons = evaluate_hard_fails(analysis, settings)

    assert [r.code for r in reasons] == ["missing_certification"]
    assert "PRINCE2" in reasons[0].message
    assert "istqb" not in reasons[0].message.lower()


def test_security_clearance_allowed_when_configured():
    settings = ScoringSettings(hard_fail=HardFailSettings(has_security_clearance=True))

    assert codes(LeadAnalysis(requires_security_clearance=True), settings) == []


def test_multiple_hard_fails_are_all_reported():
    analysis = LeadAnalysis(
        engagement_type=EngagementType.PERMANENT_EMPLOYMENT,
        requires_security_clearance=True,
        estimated_person_days=200,
    )

    assert codes(analysis) == ["security_clearance", "permanent_employment", "scope_too_large"]


@pytest.mark.parametrize("case", [c for c in ALL_CASES if c.expected_hard_fail_codes], ids=lambda c: c.name)
def test_realistic_hard_fail_cases_have_readable_messages(case):
    reasons = evaluate_hard_fails(case.analysis, SETTINGS)

    assert [r.code for r in reasons] == case.expected_hard_fail_codes
    assert all(len(r.message) > 10 for r in reasons)
