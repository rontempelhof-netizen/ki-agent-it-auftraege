"""Aufzählungstypen des Domänenmodells.

Werte sind stabile Strings: Sie werden so in SQLite gespeichert und vom LLM erwartet.
"""

from __future__ import annotations

from enum import StrEnum


class LeadCategory(StrEnum):
    DEVELOPMENT = "development"
    WEB_SHOP = "web_shop"
    MOBILE_DESKTOP = "mobile_desktop"
    INTEGRATION_API = "integration_api"
    AUTOMATION = "automation"
    DATA_SQL = "data_sql"
    AI = "ai"
    CONSULTING = "consulting"
    INFRASTRUCTURE = "infrastructure"
    OTHER = "other"


class RemoteStatus(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"


class Region(StrEnum):
    DACH = "dach"
    EU = "eu"
    INTERNATIONAL = "international"


class CustomerType(StrEnum):
    DIRECT_SME = "direct_sme"
    DIRECT_ENTERPRISE = "direct_enterprise"
    AGENCY = "agency"
    RECRUITER = "recruiter"
    PUBLIC_SECTOR = "public_sector"


class EngagementType(StrEnum):
    PROJECT = "project"
    PERMANENT_EMPLOYMENT = "permanent_employment"
    STAFF_LEASING = "staff_leasing"


class BudgetType(StrEnum):
    FIXED = "fixed"
    HOURLY = "hourly"
    DAILY = "daily"


class ScopeSize(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    VERY_LARGE = "very_large"


class ScopeClarity(StrEnum):
    CLEAR = "clear"
    PARTIAL = "partial"
    VAGUE = "vague"


class Level(StrEnum):
    """Ordinale Einschätzung (z. B. Dringlichkeit, technischer Fit)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class LeadClass(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    REJECT = "REJECT"


class LeadStatus(StrEnum):
    NEW = "NEW"
    REVIEWED = "REVIEWED"
    INTERESTING = "INTERESTING"
    CONTACTED = "CONTACTED"
    RESPONSE = "RESPONSE"
    MEETING = "MEETING"
    OFFER = "OFFER"
    WON = "WON"
    LOST = "LOST"
    REJECTED = "REJECTED"


class ProcessingStatus(StrEnum):
    """Technischer Verarbeitungsstand eines Leads (unabhängig vom Vertriebsstatus)."""

    ANALYZED = "analyzed"
    PREFILTERED = "prefiltered"
    PENDING_ANALYSIS = "pending_analysis"
    """Noch nicht analysiert (LLM-Limit erreicht oder vorübergehender LLM-Fehler)."""
    ANALYSIS_FAILED = "analysis_failed"
    """Analyse endgültig fehlgeschlagen (maximale Fehlversuche erreicht)."""


class CrawlRunStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class ScoreCriterion(StrEnum):
    """Score-Kriterien gemäß docs/requirements.md, Abschnitt 8."""

    SCOPE = "scope"
    DELIVERABILITY = "deliverability"
    WIN_PROBABILITY = "win_probability"
    BUDGET_EFFORT = "budget_effort"
    DIRECT_CUSTOMER = "direct_customer"
    REMOTE_FIT = "remote_fit"
    URGENCY = "urgency"
    FOLLOW_UP = "follow_up"
    CONSULTING_FIT = "consulting_fit"
