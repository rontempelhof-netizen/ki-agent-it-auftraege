"""Aufbereitung eines Crawl-Laufs zum Tagesreport (Ansichtsmodell, ohne Darstellung)."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel

from src.config import ReportSettings
from src.domain.enums import BudgetType, LeadCategory, LeadClass, RemoteStatus
from src.domain.models import CrawlRun, Lead

CATEGORY_LABELS: dict[LeadCategory, str] = {
    LeadCategory.DEVELOPMENT: "Softwareentwicklung",
    LeadCategory.WEB_SHOP: "Web/Shop",
    LeadCategory.MOBILE_DESKTOP: "Mobile/Desktop",
    LeadCategory.INTEGRATION_API: "Schnittstellen/API",
    LeadCategory.AUTOMATION: "Automatisierung",
    LeadCategory.DATA_SQL: "Daten/SQL",
    LeadCategory.AI: "KI",
    LeadCategory.CONSULTING: "Beratung",
    LeadCategory.INFRASTRUCTURE: "Infrastruktur",
    LeadCategory.OTHER: "Sonstiges",
}
REMOTE_LABELS: dict[RemoteStatus, str] = {
    RemoteStatus.REMOTE: "Remote",
    RemoteStatus.HYBRID: "Hybrid",
    RemoteStatus.ONSITE: "Vor Ort",
}
BUDGET_SUFFIX: dict[BudgetType, str] = {
    BudgetType.HOURLY: "/h",
    BudgetType.DAILY: "/Tag",
    BudgetType.FIXED: " (Festpreis)",
}
UNKNOWN = "unbekannt"
_CLASS_ORDER = {LeadClass.A: 0, LeadClass.B: 1, LeadClass.C: 2, LeadClass.REJECT: 3}


class ReportLead(BaseModel):
    id: int | None
    lead_class: LeadClass
    score: int
    title: str
    customer: str
    source: str
    category: str
    work_mode: str
    budget: str
    effort: str
    summary: str | None
    fit_reason: str | None
    must_haves: list[str]
    risks: list[str]
    open_questions: list[str]
    next_step: str | None
    outreach: str | None
    url: str | None
    """Nur http(s)-Links; andere Schemata werden verworfen."""


class DailyReport(BaseModel):
    title: str
    run_id: int
    run_status: str
    run_started_at: datetime
    run_finished_at: datetime | None
    generated_at: datetime
    stats: dict[str, int]
    class_counts: dict[str, int]
    leads: list[ReportLead]
    warnings: list[str]
    errors: list[str]
    llm_cost_usd: float | None
    timezone: str = "Europe/Berlin"

    @property
    def subject(self) -> str:
        return (
            f"{self.title}: {self.class_counts['A']} A-, {self.class_counts['B']} B-Leads "
            f"({self.run_started_at:%d.%m.%Y})"
        )


def _money(value: float) -> str:
    text = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return text.removesuffix(",00")


def format_budget(lead: Lead) -> str:
    if lead.budget_min is None and lead.budget_max is None:
        return UNKNOWN
    currency = lead.currency or "(Währung unbekannt)"
    suffix = BUDGET_SUFFIX.get(lead.budget_type, "") if lead.budget_type else ""
    low, high = lead.budget_min, lead.budget_max
    if low is not None and high is not None and low != high:
        amount = f"{_money(low)}–{_money(high)}"
    elif low is not None and high is None:
        amount = f"ab {_money(low)}"
    elif low is None and high is not None:
        amount = f"bis {_money(high)}"
    else:
        amount = _money(low)  # type: ignore[arg-type]
    return f"{amount} {currency}{suffix}"


def format_effort(lead: Lead) -> str:
    return f"ca. {lead.estimated_person_days:g} PT" if lead.estimated_person_days else UNKNOWN


def format_work_mode(lead: Lead) -> str:
    parts = [REMOTE_LABELS[lead.remote_status]] if lead.remote_status else []
    if lead.location and lead.location.casefold() not in {p.casefold() for p in parts}:
        parts.append(lead.location)
    return " · ".join(parts) or UNKNOWN


def safe_url(url: str | None) -> str | None:
    if not url:
        return None
    return url if urlsplit(url).scheme.lower() in {"http", "https"} else None


def to_report_lead(lead: Lead) -> ReportLead:
    assert lead.lead_class is not None
    return ReportLead(
        id=lead.id,
        lead_class=lead.lead_class,
        score=lead.score_total,
        title=lead.title,
        customer=lead.customer_name or UNKNOWN,
        source=lead.source,
        category=CATEGORY_LABELS[lead.category] if lead.category else UNKNOWN,
        work_mode=format_work_mode(lead),
        budget=format_budget(lead),
        effort=format_effort(lead),
        summary=lead.summary,
        fit_reason=lead.fit_reason,
        must_haves=lead.must_have_requirements,
        risks=lead.risks,
        open_questions=lead.open_questions,
        next_step=lead.suggested_next_step,
        outreach=lead.suggested_outreach,
        url=safe_url(lead.source_url),
    )


def build_report(run: CrawlRun, leads: list[Lead], settings: ReportSettings, generated_at: datetime) -> DailyReport:
    """A vor B (konfigurierbar), innerhalb der Klasse nach Score absteigend; C/REJECT nur gezählt."""
    assert run.id is not None
    counts = {cls.value: 0 for cls in LeadClass}
    counts["pending"] = 0
    for lead in leads:
        counts[lead.lead_class.value if lead.lead_class else "pending"] += 1

    detailed = [lead for lead in leads if lead.lead_class in settings.detail_classes]
    detailed.sort(key=lambda lead: (_CLASS_ORDER[lead.lead_class], -lead.score_total, lead.id or 0))  # type: ignore[index]
    micro = run.stats.get("llm_cost_microusd")
    return DailyReport(
        title=settings.title,
        run_id=run.id,
        run_status=run.status.value,
        run_started_at=run.started_at,
        run_finished_at=run.finished_at,
        generated_at=generated_at,
        stats=run.stats,
        class_counts=counts,
        leads=[to_report_lead(lead) for lead in detailed],
        warnings=run.warnings,
        errors=run.error_message.split("\n") if run.error_message else [],
        llm_cost_usd=micro / 1_000_000 if micro else None,
        timezone=settings.timezone,
    )
