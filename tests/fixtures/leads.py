"""Realistische Test-Fixtures für Analyse, Scoring und Persistenz.

Jeder Score-Fall enthält die von Hand berechnete Erwartung je Kriterium
(Standardgewichte: 20/15/20/15/10/5/5/5/5, unknown_fraction 0.4, Rundung half-up).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.domain.enums import (
    BudgetType,
    CustomerType,
    EngagementType,
    LeadCategory,
    LeadClass,
    Level,
    Region,
    RemoteStatus,
    ScopeClarity,
    ScopeSize,
)
from src.domain.models import LeadAnalysis, LeadCandidate

SEEN_AT = datetime(2026, 9, 30, 7, 15, tzinfo=UTC)


def make_candidate(**overrides: object) -> LeadCandidate:
    data: dict[str, object] = {
        "source": "freelancermap",
        "source_id": "fm-2981734",
        "source_url": "https://www.freelancermap.de/projekt/shopware-6-checkout-bugfix-2981734",
        "title": "Shopware 6: Fehler im Checkout beheben (Plugin-Konflikt)",
        "description": (
            "Nach einem Update auf Shopware 6.5 bricht der Checkout bei PayPal-Zahlungen sporadisch ab. "
            "Gesucht wird Unterstützung zur Analyse und Behebung des Konflikts zwischen zwei Plugins. "
            "Zugang zu Staging vorhanden. Start ab sofort, Remote."
        ),
        "published_at": datetime(2026, 9, 29, 16, 0, tzinfo=UTC),
        "first_seen_at": SEEN_AT,
        "location": "Remote",
        "remote_status": RemoteStatus.REMOTE,
        "language": "de",
        "customer_name": "Gartenbedarf Müller GmbH",
    }
    data.update(overrides)
    return LeadCandidate.model_validate(data)


@dataclass(frozen=True)
class ScoreCase:
    name: str
    analysis: LeadAnalysis
    expected_points: dict[str, int]
    expected_class: LeadClass
    expected_hard_fail_codes: list[str] = field(default_factory=list)

    @property
    def expected_total(self) -> int:
        return sum(self.expected_points.values())


def _points(
    scope: int,
    deliverability: int,
    win_probability: int,
    budget_effort: int,
    direct_customer: int,
    remote_fit: int,
    urgency: int,
    follow_up: int,
    consulting_fit: int,
) -> dict[str, int]:
    return {
        "scope": scope,
        "deliverability": deliverability,
        "win_probability": win_probability,
        "budget_effort": budget_effort,
        "direct_customer": direct_customer,
        "remote_fit": remote_fit,
        "urgency": urgency,
        "follow_up": follow_up,
        "consulting_fit": consulting_fit,
    }


# --- Realistische Fälle ---------------------------------------------------

SHOPWARE_BUGFIX = ScoreCase(
    name="shopware_checkout_bugfix",
    analysis=LeadAnalysis(
        summary="PayPal-Checkout bricht nach Shopware-6.5-Update wegen Plugin-Konflikt ab.",
        category=LeadCategory.WEB_SHOP,
        required_skills=["Shopware 6", "PHP", "Symfony"],
        must_have_requirements=["Shopware-6-Erfahrung", "Plugin-Entwicklung", "PayPal-Integration"],
        engagement_type=EngagementType.PROJECT,
        scope_size=ScopeSize.SMALL,
        scope_clarity=ScopeClarity.CLEAR,
        estimated_person_days=5,
        budget_min=4000,
        budget_max=4000,
        currency="EUR",
        budget_type=BudgetType.FIXED,
        customer_name="Gartenbedarf Müller GmbH",
        customer_type=CustomerType.DIRECT_SME,
        decision_complexity=Level.LOW,
        remote_status=RemoteStatus.REMOTE,
        region=Region.DACH,
        technical_fit=Level.HIGH,
        entry_barrier=Level.LOW,
        urgency=Level.HIGH,
        follow_up_potential=Level.MEDIUM,
        consulting_fit=Level.LOW,
        fit_reason="Klar abgegrenzter Bugfix in bestehendem Shop, direkter KMU-Kunde.",
        risks=["Ursache evtl. im Payment-Provider"],
        open_questions=["Gibt es Logs vom Zeitpunkt der Abbrüche?"],
        suggested_next_step="Kurze Rückfrage zu Logs und Staging-Zugang",
        suggested_outreach="Guten Tag Herr Müller, ...",
    ),
    # scope: 5 PT ≤ 20 -> 1.0, klar -> 1.0 => 20 | budget: 4000/5 = 800 EUR/Tag ≥ 800 => 15
    # direct: 0.7*1.0 + 0.3*1.0 => 10 | follow_up MEDIUM 2.5 -> 3
    expected_points=_points(20, 15, 20, 15, 10, 5, 5, 3, 0),
    expected_class=LeadClass.A,
)

ERP_SHOP_API_VIA_AGENCY = ScoreCase(
    name="erp_shop_api_via_agency",
    analysis=LeadAnalysis(
        summary="Anbindung des Warenwirtschaftssystems an WooCommerce per REST-API (Bestände, Aufträge).",
        category=LeadCategory.INTEGRATION_API,
        required_skills=["REST", "PHP", "WooCommerce", "SQL"],
        must_have_requirements=["REST-API", "WooCommerce", "SQL", "Erfahrung ERP-Schnittstellen", "Deutsch"],
        engagement_type=EngagementType.PROJECT,
        scope_clarity=ScopeClarity.PARTIAL,
        estimated_person_days=15,
        budget_min=90,
        budget_max=100,
        currency="EUR",
        budget_type=BudgetType.HOURLY,
        customer_type=CustomerType.AGENCY,
        decision_complexity=Level.MEDIUM,
        remote_status=RemoteStatus.HYBRID,
        region=Region.DACH,
        location="Stuttgart",
        technical_fit=Level.HIGH,
        entry_barrier=Level.MEDIUM,
        urgency=Level.MEDIUM,
        follow_up_potential=Level.HIGH,
        consulting_fit=Level.MEDIUM,
    ),
    # scope: 1.0/0.5 -> 15 | win: MEDIUM 10 | budget: 95 €/h * 8 = 760 ≥ 600 -> 0.6*15 = 9
    # direct: 0.7*0.4 + 0.3*0.5 = 0.43 -> 4.3 -> 4 | remote: hybrid DACH 0.8*5 = 4
    expected_points=_points(15, 15, 10, 9, 4, 4, 3, 5, 3),
    expected_class=LeadClass.B,
)

PERFECT_SMALL_PROJECT = ScoreCase(
    name="perfect_small_project",
    analysis=LeadAnalysis(
        summary="Excel-basierte Angebotskalkulation als kleines internes Web-Tool umsetzen.",
        category=LeadCategory.AUTOMATION,
        engagement_type=EngagementType.PROJECT,
        scope_size=ScopeSize.SMALL,
        scope_clarity=ScopeClarity.CLEAR,
        estimated_person_days=5,
        budget_min=5000,
        budget_max=5000,
        currency="EUR",
        budget_type=BudgetType.FIXED,
        customer_type=CustomerType.DIRECT_SME,
        decision_complexity=Level.LOW,
        remote_status=RemoteStatus.REMOTE,
        region=Region.DACH,
        technical_fit=Level.HIGH,
        entry_barrier=Level.LOW,
        urgency=Level.HIGH,
        follow_up_potential=Level.HIGH,
        consulting_fit=Level.HIGH,
    ),
    expected_points=_points(20, 15, 20, 15, 10, 5, 5, 5, 5),
    expected_class=LeadClass.A,
)

ALL_UNKNOWN = ScoreCase(
    name="all_unknown",
    analysis=LeadAnalysis(summary="Unterstützung bei IT-Projekt gesucht."),
    # jedes Kriterium 0.4 * max: 8/6/8/6/4/2/2/2/2
    expected_points=_points(8, 6, 8, 6, 4, 2, 2, 2, 2),
    expected_class=LeadClass.REJECT,
)

# --- Grenzwerte (je ein Merkmal Unterschied) -------------------------------


def _csv_import(decision: Level) -> LeadAnalysis:
    return LeadAnalysis(
        summary="Täglicher CSV-Import von Lieferantenpreisen in MS-SQL-Datenbank automatisieren.",
        category=LeadCategory.DATA_SQL,
        required_skills=["Python", "MS SQL"],
        engagement_type=EngagementType.PROJECT,
        scope_clarity=ScopeClarity.CLEAR,
        estimated_person_days=10,
        budget_min=650,
        currency="EUR",
        budget_type=BudgetType.DAILY,
        customer_type=CustomerType.DIRECT_SME,
        decision_complexity=decision,
        remote_status=RemoteStatus.REMOTE,
        technical_fit=Level.HIGH,
        entry_barrier=Level.MEDIUM,
        urgency=Level.HIGH,
        follow_up_potential=Level.MEDIUM,
        consulting_fit=Level.MEDIUM,
    )


CSV_IMPORT_79 = ScoreCase(
    name="csv_import_decision_medium_79",
    analysis=_csv_import(Level.MEDIUM),
    # direct: 0.7 + 0.15 = 0.85 -> 8.5 -> 9 | budget 650 ≥ 600 -> 9
    expected_points=_points(20, 15, 10, 9, 9, 5, 5, 3, 3),
    expected_class=LeadClass.B,
)
CSV_IMPORT_80 = ScoreCase(
    name="csv_import_decision_low_80",
    analysis=_csv_import(Level.LOW),
    expected_points=_points(20, 15, 10, 9, 10, 5, 5, 3, 3),
    expected_class=LeadClass.A,
)


def _process_consulting(urgency: Level | None) -> LeadAnalysis:
    return LeadAnalysis(
        summary="Handwerksbetrieb möchte Angebots- und Auftragsprozess digitalisieren; Ziel noch unscharf.",
        category=LeadCategory.CONSULTING,
        engagement_type=EngagementType.PROJECT,
        scope_clarity=ScopeClarity.VAGUE,
        estimated_person_days=8,
        remote_status=RemoteStatus.HYBRID,
        region=Region.DACH,
        location="Raum Augsburg",
        technical_fit=Level.MEDIUM,
        entry_barrier=Level.LOW,
        urgency=urgency,
        follow_up_potential=Level.HIGH,
        consulting_fit=Level.HIGH,
    )


PROCESS_CONSULTING_64 = ScoreCase(
    name="process_consulting_urgency_unknown_64",
    analysis=_process_consulting(None),
    # scope: 0.5*1.0 + 0.5*0 -> 10 | technical MEDIUM 7.5 -> 8 | budget unbekannt 6
    # direct: Kundenart/Entscheidung unbekannt -> 4 | urgency unbekannt 2
    expected_points=_points(10, 8, 20, 6, 4, 4, 2, 5, 5),
    expected_class=LeadClass.C,
)
PROCESS_CONSULTING_65 = ScoreCase(
    name="process_consulting_urgency_medium_65",
    analysis=_process_consulting(Level.MEDIUM),
    expected_points=_points(10, 8, 20, 6, 4, 4, 3, 5, 5),
    expected_class=LeadClass.B,
)


def _legacy_php_via_recruiter(region: Region | None) -> LeadAnalysis:
    return LeadAnalysis(
        summary="Wartung und Weiterentwicklung einer Legacy-PHP-Anwendung (PHP 5.6 -> 8) über Personalvermittler.",
        category=LeadCategory.DEVELOPMENT,
        required_skills=["PHP", "MySQL", "Legacy-Modernisierung"],
        engagement_type=EngagementType.PROJECT,
        scope_clarity=ScopeClarity.PARTIAL,
        estimated_person_days=30,
        budget_max=70,
        currency="EUR",
        budget_type=BudgetType.HOURLY,
        customer_type=CustomerType.RECRUITER,
        decision_complexity=Level.HIGH,
        remote_status=RemoteStatus.HYBRID,
        region=region,
        technical_fit=Level.MEDIUM,
        entry_barrier=Level.MEDIUM,
        urgency=Level.HIGH,
        follow_up_potential=Level.HIGH,
        consulting_fit=Level.MEDIUM,
    )


LEGACY_PHP_49 = ScoreCase(
    name="legacy_php_region_unknown_49",
    analysis=_legacy_php_via_recruiter(None),
    # scope: 30 PT -> 0.5, partial 0.5 -> 10 | budget: 70*8 = 560 -> 0.2*15 = 3
    # direct: 0.7*0.3 + 0 = 0.21 -> 2 | remote: hybrid, Region unbekannt 0.5*5 = 2.5 -> 3
    expected_points=_points(10, 8, 10, 3, 2, 3, 5, 5, 3),
    expected_class=LeadClass.REJECT,
)
LEGACY_PHP_50 = ScoreCase(
    name="legacy_php_region_dach_50",
    analysis=_legacy_php_via_recruiter(Region.DACH),
    expected_points=_points(10, 8, 10, 3, 2, 4, 5, 5, 3),
    expected_class=LeadClass.C,
)

# --- Hard Fails ------------------------------------------------------------

SAP_STAFF_LEASING = ScoreCase(
    name="sap_migration_staff_leasing",
    analysis=LeadAnalysis(
        summary="S/4HANA-Migration im Konzern, 12 Monate Vollzeit vor Ort, Einsatz über Dienstleister.",
        category=LeadCategory.CONSULTING,
        required_skills=["SAP S/4HANA", "ABAP"],
        must_have_requirements=[f"Muss-Anforderung {i}" for i in range(1, 17)],
        engagement_type=EngagementType.STAFF_LEASING,
        scope_size=ScopeSize.VERY_LARGE,
        scope_clarity=ScopeClarity.PARTIAL,
        estimated_person_days=220,
        budget_max=85,
        currency="EUR",
        budget_type=BudgetType.HOURLY,
        customer_type=CustomerType.AGENCY,
        decision_complexity=Level.HIGH,
        remote_status=RemoteStatus.ONSITE,
        region=Region.DACH,
        location="Walldorf",
        technical_fit=Level.LOW,
        entry_barrier=Level.HIGH,
        urgency=Level.MEDIUM,
        follow_up_potential=Level.LOW,
        consulting_fit=Level.LOW,
    ),
    # scope: 220 PT -> 0, partial -> 0.25*20 = 5 | win: HIGH 0 - Abzug -> 0
    # budget: 85*8 = 680 -> 9 | direct: 0.28 + 0 -> 2.8 -> 3 | onsite DACH 0.6*5 = 3
    expected_points=_points(5, 0, 0, 9, 3, 3, 3, 0, 0),
    expected_class=LeadClass.REJECT,
    expected_hard_fail_codes=["staff_leasing", "scope_too_large", "too_many_must_haves"],
)

SECURITY_CLEARANCE_PUBLIC = ScoreCase(
    name="public_sector_security_clearance",
    analysis=LeadAnalysis(
        summary="Weiterentwicklung einer Fachanwendung bei einer Bundesbehörde, Ü2 erforderlich.",
        category=LeadCategory.DEVELOPMENT,
        required_certifications=["ISTQB Foundation"],
        requires_security_clearance=True,
        engagement_type=EngagementType.PROJECT,
        scope_clarity=ScopeClarity.CLEAR,
        estimated_person_days=20,
        customer_type=CustomerType.PUBLIC_SECTOR,
        decision_complexity=Level.HIGH,
        remote_status=RemoteStatus.HYBRID,
        region=Region.DACH,
        technical_fit=Level.HIGH,
        entry_barrier=Level.HIGH,
    ),
    # scope 20 | win HIGH 0 | budget unbekannt 6 | direct 0.7*0.2 = 0.14 -> 1.4 -> 1 | hybrid DACH 4
    expected_points=_points(20, 15, 0, 6, 1, 4, 2, 2, 2),
    expected_class=LeadClass.REJECT,
    expected_hard_fail_codes=["missing_certification", "security_clearance"],
)

PERMANENT_POSITION = ScoreCase(
    name="permanent_position",
    analysis=LeadAnalysis(
        summary="Festanstellung als Senior Java Developer (m/w/d), unbefristet.",
        category=LeadCategory.DEVELOPMENT,
        engagement_type=EngagementType.PERMANENT_EMPLOYMENT,
        customer_type=CustomerType.DIRECT_ENTERPRISE,
        remote_status=RemoteStatus.HYBRID,
        region=Region.DACH,
        technical_fit=Level.MEDIUM,
    ),
    # direct: 0.7*0.6 + 0.3*0.4 = 0.54 -> 5.4 -> 5
    expected_points=_points(8, 8, 8, 6, 5, 4, 2, 2, 2),
    expected_class=LeadClass.REJECT,
    expected_hard_fail_codes=["permanent_employment"],
)

UPWORK_LOW_RATE = ScoreCase(
    name="upwork_low_hourly_rate",
    analysis=LeadAnalysis(
        summary="Build a Flutter app MVP with Firebase backend.",
        category=LeadCategory.MOBILE_DESKTOP,
        engagement_type=EngagementType.PROJECT,
        scope_clarity=ScopeClarity.PARTIAL,
        estimated_person_days=25,
        budget_min=25,
        budget_max=35,
        currency="USD",
        budget_type=BudgetType.HOURLY,
        customer_type=CustomerType.DIRECT_SME,
        remote_status=RemoteStatus.REMOTE,
        region=Region.INTERNATIONAL,
        language="en",
        technical_fit=Level.MEDIUM,
        entry_barrier=Level.MEDIUM,
        urgency=Level.MEDIUM,
        follow_up_potential=Level.MEDIUM,
        consulting_fit=Level.LOW,
    ),
    # scope: 25 PT -> 0.5, partial 0.5 -> 10 | budget: 30 USD * 0.92 * 8 = 220.8 EUR -> 0
    # direct: 0.7 + 0.3*0.4 = 0.82 -> 8
    expected_points=_points(10, 8, 10, 0, 8, 5, 3, 3, 0),
    expected_class=LeadClass.REJECT,
    expected_hard_fail_codes=["unrealistic_budget"],
)

ALL_CASES: list[ScoreCase] = [
    SHOPWARE_BUGFIX,
    ERP_SHOP_API_VIA_AGENCY,
    PERFECT_SMALL_PROJECT,
    ALL_UNKNOWN,
    CSV_IMPORT_79,
    CSV_IMPORT_80,
    PROCESS_CONSULTING_64,
    PROCESS_CONSULTING_65,
    LEGACY_PHP_49,
    LEGACY_PHP_50,
    SAP_STAFF_LEASING,
    SECURITY_CLEARANCE_PUBLIC,
    PERMANENT_POSITION,
    UPWORK_LOW_RATE,
]
