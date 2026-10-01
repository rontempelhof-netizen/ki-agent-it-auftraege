from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.config import ScoringSettings
from src.domain.enums import BudgetType, LeadClass, LeadStatus, RemoteStatus
from src.domain.models import (
    Feedback,
    Lead,
    LeadAnalysis,
    RawSourceItem,
    merge_source_facts,
)
from src.scoring.engine import ScoreEngine
from tests.fixtures.leads import ALL_UNKNOWN, SEEN_AT, SHOPWARE_BUGFIX, make_candidate

ENGINE = ScoreEngine(ScoringSettings())


class TestRawSourceItem:
    def test_valid_item(self):
        item = RawSourceItem(
            source="freelance.de",
            received_at=SEEN_AT,
            title="Neues Projekt: Python-Entwickler (remote)",
            body_text="Projektbeschreibung ...",
            metadata={"from": "noreply@freelance.de"},
        )
        assert item.source_item_id is None

    def test_requires_some_content(self):
        with pytest.raises(ValidationError, match="benötigt"):
            RawSourceItem(source="freelance.de", received_at=SEEN_AT)

    def test_naive_datetime_rejected(self):
        with pytest.raises(ValidationError):
            RawSourceItem(source="x", received_at=datetime(2026, 1, 1), title="t")


class TestLeadCandidate:
    def test_valid_candidate(self):
        candidate = make_candidate()
        assert candidate.remote_status == RemoteStatus.REMOTE
        assert candidate.budget_min is None

    @pytest.mark.parametrize("field", ["source", "source_id", "title"])
    def test_required_fields_must_not_be_empty(self, field):
        with pytest.raises(ValidationError):
            make_candidate(**{field: "  "})


class TestLeadAnalysis:
    def test_all_fields_optional(self):
        analysis = LeadAnalysis()
        assert analysis.category is None
        assert analysis.required_skills == []
        assert analysis.estimated_person_days is None

    def test_score_field_from_llm_is_rejected(self):
        with pytest.raises(ValidationError):
            LeadAnalysis.model_validate({"summary": "x", "score_total": 95})

    def test_lists_are_cleaned(self):
        analysis = LeadAnalysis.model_validate(
            {"required_skills": ["Python", " python ", "", "SQL"], "risks": None}
        )
        assert analysis.required_skills == ["Python", "SQL"]
        assert analysis.risks == []

    def test_currency_normalized(self):
        assert LeadAnalysis(currency=" eur ").currency == "EUR"

    @pytest.mark.parametrize("currency", ["EURO", "€", "12"])
    def test_invalid_currency(self, currency):
        with pytest.raises(ValidationError):
            LeadAnalysis(currency=currency)

    def test_budget_range_validated(self):
        with pytest.raises(ValidationError, match="budget_min"):
            LeadAnalysis(budget_min=5000, budget_max=1000)

    @pytest.mark.parametrize("days", [0, -1, 20_000])
    def test_invalid_person_days(self, days):
        with pytest.raises(ValidationError):
            LeadAnalysis(estimated_person_days=days)

    def test_invalid_enum_value(self):
        with pytest.raises(ValidationError):
            LeadAnalysis.model_validate({"remote_status": "sometimes"})

    def test_json_roundtrip(self):
        data = SHOPWARE_BUGFIX.analysis.model_dump(mode="json")
        assert LeadAnalysis.model_validate(data) == SHOPWARE_BUGFIX.analysis


class TestMergeSourceFacts:
    def test_source_budget_block_wins_over_llm(self):
        candidate = make_candidate(budget_min=85, budget_max=95, currency="EUR", budget_type=BudgetType.HOURLY)

        merged = merge_source_facts(candidate, SHOPWARE_BUGFIX.analysis)

        assert (merged.budget_min, merged.budget_max, merged.budget_type) == (85, 95, BudgetType.HOURLY)
        assert merged.summary == SHOPWARE_BUGFIX.analysis.summary

    def test_missing_source_facts_keep_analysis_values(self):
        candidate = make_candidate(remote_status=None, location=None, customer_name=None, language=None)

        assert merge_source_facts(candidate, SHOPWARE_BUGFIX.analysis) == SHOPWARE_BUGFIX.analysis

    def test_unknown_stays_unknown(self):
        candidate = make_candidate(remote_status=None, location=None, customer_name=None, language=None)

        merged = merge_source_facts(candidate, ALL_UNKNOWN.analysis)

        assert merged.budget_min is None and merged.remote_status is None

    def test_merge_is_idempotent(self):
        candidate = make_candidate(budget_min=4000, currency="EUR", budget_type=BudgetType.FIXED)
        once = merge_source_facts(candidate, ALL_UNKNOWN.analysis)

        assert merge_source_facts(candidate, once) == once


class TestLead:
    def test_build_complete_lead(self):
        candidate = make_candidate()
        score = ENGINE.score(merge_source_facts(candidate, SHOPWARE_BUGFIX.analysis))

        lead = Lead.build(candidate, SHOPWARE_BUGFIX.analysis, score, prompt_version="analysis-v1")

        assert lead.title == candidate.title
        assert lead.source_url == candidate.source_url
        assert lead.score_total == 93
        assert lead.lead_class == LeadClass.A
        assert lead.status == LeadStatus.NEW
        assert lead.hard_fail is False
        assert lead.must_have_requirements == SHOPWARE_BUGFIX.analysis.must_have_requirements
        assert sum(i.points for i in lead.score_breakdown) == lead.score_total
        assert lead.prompt_version == "analysis-v1"
        assert lead.analysis is not None

    def test_build_with_missing_optional_data(self):
        candidate = make_candidate(
            source_url=None, published_at=None, location=None, remote_status=None, language=None, customer_name=None
        )
        score = ENGINE.score(ALL_UNKNOWN.analysis)

        lead = Lead.build(candidate, ALL_UNKNOWN.analysis, score)

        assert lead.budget_min is None and lead.currency is None
        assert lead.customer_name is None and lead.customer_type is None
        assert lead.estimated_person_days is None
        assert lead.risks == [] and lead.open_questions == []
        assert lead.score_total == 40
        assert lead.lead_class == LeadClass.REJECT

    def test_build_with_hard_fail(self):
        analysis = SHOPWARE_BUGFIX.analysis.model_copy(update={"requires_security_clearance": True})
        candidate = make_candidate()
        lead = Lead.build(candidate, analysis, ENGINE.score(analysis))

        assert lead.hard_fail
        assert lead.hard_fail_reasons == ["Sicherheitsüberprüfung/-freigabe erforderlich"]
        assert lead.lead_class == LeadClass.REJECT

    def test_score_total_must_match_breakdown(self):
        lead = Lead.build(make_candidate(), SHOPWARE_BUGFIX.analysis, ENGINE.score(SHOPWARE_BUGFIX.analysis))
        data = lead.model_dump() | {"score_total": 50}

        with pytest.raises(ValidationError, match="score_total"):
            Lead.model_validate(data)

    def test_hard_fail_requires_reject_class(self):
        with pytest.raises(ValidationError, match="REJECT"):
            Lead(
                source="x", source_id="1", title="t", first_seen_at=SEEN_AT,
                hard_fail=True, hard_fail_reasons=["Grund"], lead_class=LeadClass.A,
            )

    def test_hard_fail_flag_and_reasons_consistent(self):
        with pytest.raises(ValidationError, match="inkonsistent"):
            Lead(source="x", source_id="1", title="t", first_seen_at=SEEN_AT, hard_fail=True)


class TestFeedback:
    def test_valid(self):
        assert Feedback(lead_id=1, created_at=datetime.now(UTC), rating=4).rating == 4

    def test_requires_content(self):
        with pytest.raises(ValidationError):
            Feedback(lead_id=1, created_at=datetime.now(UTC))

    @pytest.mark.parametrize("rating", [0, 6])
    def test_rating_range(self, rating):
        with pytest.raises(ValidationError):
            Feedback(lead_id=1, created_at=datetime.now(UTC), rating=rating)
