"""Pydantic-Modelle des Datenflusses.

Source -> RawSourceItem -> LeadCandidate -> LeadAnalysis -> ScoreResult -> Lead

Grundsatz: Unbekannte Informationen bleiben ``None`` bzw. leere Liste und werden
niemals durch Annahmen ersetzt.
"""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from src.domain.enums import (
    BudgetType,
    CrawlRunStatus,
    CustomerType,
    EngagementType,
    LeadCategory,
    LeadClass,
    LeadStatus,
    Level,
    Region,
    RemoteStatus,
    ScopeClarity,
    ScopeSize,
    ScoreCriterion,
)


def _clean_string_list(values: list[str]) -> list[str]:
    """Entfernt leere Einträge und Duplikate (case-insensitiv), Reihenfolge bleibt erhalten."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        stripped = value.strip()
        if stripped and stripped.casefold() not in seen:
            seen.add(stripped.casefold())
            result.append(stripped)
    return result


def _none_to_empty_list(value: object) -> object:
    return [] if value is None else value


def _upper(value: object) -> object:
    return value.strip().upper() if isinstance(value, str) else value


StringList = Annotated[
    list[str],
    BeforeValidator(_none_to_empty_list),
    AfterValidator(_clean_string_list),
]
CurrencyCode = Annotated[str, BeforeValidator(_upper), Field(pattern=r"^[A-Z]{3}$")]
Money = Annotated[float, Field(ge=0)]
PersonDays = Annotated[float, Field(gt=0, le=10_000)]


class DomainModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)


class _BudgetMixin(BaseModel):
    budget_min: Money | None = None
    budget_max: Money | None = None
    currency: CurrencyCode | None = None
    budget_type: BudgetType | None = None

    @model_validator(mode="after")
    def _budget_range(self) -> Self:
        if self.budget_min is not None and self.budget_max is not None and self.budget_min > self.budget_max:
            raise ValueError("budget_min darf nicht größer als budget_max sein")
        return self


class RawSourceItem(DomainModel):
    """Unveränderter Eintrag, wie ihn ein Source Connector liefert (untrusted input)."""

    source: str = Field(min_length=1)
    source_item_id: str | None = None
    source_url: str | None = None
    received_at: AwareDatetime
    title: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    metadata: dict[str, str] = {}

    @model_validator(mode="after")
    def _has_content(self) -> Self:
        if not (self.title or self.body_text or self.body_html):
            raise ValueError("RawSourceItem benötigt title, body_text oder body_html")
        return self


class LeadCandidate(_BudgetMixin, DomainModel):
    """Normalisierter Auftrag vor der LLM-Analyse. Enthält nur Fakten aus der Quelle."""

    source: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_url: str | None = None
    title: str = Field(min_length=1)
    description: str = ""
    published_at: AwareDatetime | None = None
    first_seen_at: AwareDatetime
    location: str | None = None
    remote_status: RemoteStatus | None = None
    language: str | None = None
    customer_name: str | None = None


class LeadAnalysis(_BudgetMixin, DomainModel):
    """Strukturierte Merkmale aus der LLM-Analyse.

    Enthält bewusst keinen Score: Der Score wird deterministisch in Python berechnet.
    ``extra="forbid"`` verhindert, dass zusätzliche Felder (z. B. ein vom LLM
    geliefertes ``score``) unbemerkt übernommen werden.
    """

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    summary: str | None = None
    category: LeadCategory | None = None
    required_skills: StringList = []
    must_have_requirements: StringList = []
    required_certifications: StringList = []
    requires_security_clearance: bool | None = None
    engagement_type: EngagementType | None = None

    scope_size: ScopeSize | None = None
    scope_clarity: ScopeClarity | None = None
    estimated_person_days: PersonDays | None = None

    customer_name: str | None = None
    customer_type: CustomerType | None = None
    decision_complexity: Level | None = None

    remote_status: RemoteStatus | None = None
    region: Region | None = None
    location: str | None = None
    language: str | None = None

    high_liability: bool | None = None
    technical_fit: Level | None = None
    entry_barrier: Level | None = None
    urgency: Level | None = None
    follow_up_potential: Level | None = None
    consulting_fit: Level | None = None

    fit_reason: str | None = None
    risks: StringList = []
    open_questions: StringList = []
    suggested_next_step: str | None = None
    suggested_outreach: str | None = None


class ScoreItem(DomainModel):
    """Ein Kriterium der Score-Aufschlüsselung."""

    criterion: ScoreCriterion
    points: int = Field(ge=0)
    max_points: int = Field(ge=0)
    reason: str

    @model_validator(mode="after")
    def _points_within_max(self) -> Self:
        if self.points > self.max_points:
            raise ValueError(f"{self.criterion}: points {self.points} > max_points {self.max_points}")
        return self


class HardFailReason(DomainModel):
    code: str
    message: str


class ScoreResult(DomainModel):
    items: list[ScoreItem]
    hard_fail_reasons: list[HardFailReason] = []
    lead_class: LeadClass

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> int:
        return sum(item.points for item in self.items)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def hard_fail(self) -> bool:
        return bool(self.hard_fail_reasons)

    def points_for(self, criterion: ScoreCriterion) -> int:
        return next(item.points for item in self.items if item.criterion == criterion)


class Lead(_BudgetMixin, DomainModel):
    """Bewerteter, persistierbarer Lead (docs/requirements.md, Abschnitt 7)."""

    id: int | None = None
    source: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_url: str | None = None
    title: str = Field(min_length=1)
    description: str = ""
    published_at: AwareDatetime | None = None
    first_seen_at: AwareDatetime

    category: LeadCategory | None = None
    remote_status: RemoteStatus | None = None
    location: str | None = None
    language: str | None = None
    required_skills: StringList = []
    must_have_requirements: StringList = []
    required_certifications: StringList = []
    estimated_person_days: PersonDays | None = None
    customer_name: str | None = None
    customer_type: CustomerType | None = None

    hard_fail: bool = False
    hard_fail_reasons: StringList = []
    score_total: int = Field(0, ge=0, le=100)
    score_breakdown: list[ScoreItem] = []
    lead_class: LeadClass = LeadClass.REJECT

    summary: str | None = None
    fit_reason: str | None = None
    risks: StringList = []
    open_questions: StringList = []
    suggested_next_step: str | None = None
    suggested_outreach: str | None = None

    status: LeadStatus = LeadStatus.NEW
    prompt_version: str | None = None
    analysis: LeadAnalysis | None = None

    @model_validator(mode="after")
    def _consistent_score(self) -> Self:
        if self.score_breakdown and self.score_total != sum(i.points for i in self.score_breakdown):
            raise ValueError("score_total muss der Summe von score_breakdown entsprechen")
        if self.hard_fail != bool(self.hard_fail_reasons):
            raise ValueError("hard_fail und hard_fail_reasons sind inkonsistent")
        if self.hard_fail and self.lead_class != LeadClass.REJECT:
            raise ValueError("Leads mit Hard Fail müssen als REJECT klassifiziert sein")
        return self

    @classmethod
    def build(
        cls,
        candidate: LeadCandidate,
        analysis: LeadAnalysis,
        score: ScoreResult,
        prompt_version: str | None = None,
    ) -> Lead:
        """Führt Quelldaten, Analyse und Score zusammen.

        ``score`` muss aus ``merge_source_facts(candidate, analysis)`` berechnet
        worden sein; da das Zusammenführen idempotent ist, darf ``analysis`` bereits
        zusammengeführt übergeben werden.
        """
        merged = merge_source_facts(candidate, analysis)
        return cls(
            source=candidate.source,
            source_id=candidate.source_id,
            source_url=candidate.source_url,
            title=candidate.title,
            description=candidate.description,
            published_at=candidate.published_at,
            first_seen_at=candidate.first_seen_at,
            category=merged.category,
            remote_status=merged.remote_status,
            location=merged.location,
            language=merged.language,
            required_skills=merged.required_skills,
            must_have_requirements=merged.must_have_requirements,
            required_certifications=merged.required_certifications,
            budget_min=merged.budget_min,
            budget_max=merged.budget_max,
            currency=merged.currency,
            budget_type=merged.budget_type,
            estimated_person_days=merged.estimated_person_days,
            customer_name=merged.customer_name,
            customer_type=merged.customer_type,
            hard_fail=score.hard_fail,
            hard_fail_reasons=[r.message for r in score.hard_fail_reasons],
            score_total=score.total,
            score_breakdown=score.items,
            lead_class=score.lead_class,
            summary=merged.summary,
            fit_reason=merged.fit_reason,
            risks=merged.risks,
            open_questions=merged.open_questions,
            suggested_next_step=merged.suggested_next_step,
            suggested_outreach=merged.suggested_outreach,
            prompt_version=prompt_version,
            analysis=merged,
        )


def merge_source_facts(candidate: LeadCandidate, analysis: LeadAnalysis) -> LeadAnalysis:
    """Überträgt strukturierte Quelldaten in die Analyse (idempotent).

    Fakten aus der Quelle (z. B. Budgetfelder des Portals) haben Vorrang vor
    LLM-Extraktionen. Das Budget wird als Block übernommen, damit Betrag, Währung
    und Budgetart nicht aus verschiedenen Quellen gemischt werden.
    """
    updates: dict[str, object] = {}
    if candidate.budget_min is not None or candidate.budget_max is not None:
        updates |= {
            "budget_min": candidate.budget_min,
            "budget_max": candidate.budget_max,
            "currency": candidate.currency,
            "budget_type": candidate.budget_type,
        }
    for field in ("remote_status", "location", "language", "customer_name"):
        value = getattr(candidate, field)
        if value is not None:
            updates[field] = value
    return analysis.model_copy(update=updates) if updates else analysis


class LeadSourceRef(DomainModel):
    """Ein Vorkommen eines Leads in einer Quelle."""

    source: str = Field(min_length=1)
    source_item_id: str = Field(min_length=1)
    source_url: str | None = None
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime


class CrawlRun(DomainModel):
    id: int | None = None
    source: str | None = None
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    status: CrawlRunStatus = CrawlRunStatus.RUNNING
    items_found: int = Field(0, ge=0)
    items_new: int = Field(0, ge=0)
    items_analyzed: int = Field(0, ge=0)
    items_rejected: int = Field(0, ge=0)
    warnings: StringList = []
    error_message: str | None = None


class Feedback(DomainModel):
    """Menschliche Rückmeldung zu einem Lead (Grundlage für spätere Kalibrierung)."""

    id: int | None = None
    lead_id: int
    created_at: AwareDatetime
    relevant: bool | None = None
    rating: int | None = Field(None, ge=1, le=5)
    comment: str | None = None

    @model_validator(mode="after")
    def _has_content(self) -> Self:
        if self.relevant is None and self.rating is None and not self.comment:
            raise ValueError("Feedback benötigt relevant, rating oder comment")
        return self
