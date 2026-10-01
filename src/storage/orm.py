"""SQLAlchemy-Tabellen. Einziger Ort für Schemadefinitionen.

Tabellen gemäß docs/requirements.md, Abschnitt 11:
leads, lead_sources, lead_score_details, crawl_runs, feedback
(+ technische Tabellen app_meta, processed_emails).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.storage.base import Base, UTCDateTime


def utc_now() -> datetime:
    return datetime.now(UTC)


class AppMeta(Base):
    """Technische Metadaten (z. B. Schema-Version bis zur Einführung von Migrationen)."""

    __tablename__ = "app_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))


class LeadRow(Base):
    __tablename__ = "leads"
    __table_args__ = (
        UniqueConstraint("source", "source_id"),
        Index(None, "lead_class", "score_total"),
        Index(None, "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[str] = mapped_column(String(255))
    source_url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)

    category: Mapped[str | None] = mapped_column(String(32))
    remote_status: Mapped[str | None] = mapped_column(String(16))
    location: Mapped[str | None] = mapped_column(String(255))
    language: Mapped[str | None] = mapped_column(String(16))
    required_skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    must_have_requirements: Mapped[list[str]] = mapped_column(JSON, default=list)
    required_certifications: Mapped[list[str]] = mapped_column(JSON, default=list)

    budget_min: Mapped[float | None] = mapped_column(Float)
    budget_max: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(3))
    budget_type: Mapped[str | None] = mapped_column(String(16))
    estimated_person_days: Mapped[float | None] = mapped_column(Float)

    customer_name: Mapped[str | None] = mapped_column(String(255))
    customer_type: Mapped[str | None] = mapped_column(String(32))

    hard_fail: Mapped[bool] = mapped_column(Boolean, default=False)
    hard_fail_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    score_total: Mapped[int] = mapped_column(Integer, default=0)
    lead_class: Mapped[str] = mapped_column(String(8))

    summary: Mapped[str | None] = mapped_column(Text)
    fit_reason: Mapped[str | None] = mapped_column(Text)
    risks: Mapped[list[str]] = mapped_column(JSON, default=list)
    open_questions: Mapped[list[str]] = mapped_column(JSON, default=list)
    suggested_next_step: Mapped[str | None] = mapped_column(Text)
    suggested_outreach: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(16))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    score_details: Mapped[list[LeadScoreDetailRow]] = relationship(
        back_populates="lead", cascade="all, delete-orphan", order_by="LeadScoreDetailRow.id"
    )
    sources: Mapped[list[LeadSourceRow]] = relationship(
        back_populates="lead", cascade="all, delete-orphan", order_by="LeadSourceRow.id"
    )
    feedback: Mapped[list[FeedbackRow]] = relationship(back_populates="lead", cascade="all, delete-orphan")


class LeadSourceRow(Base):
    """Vorkommen eines Leads in einer Quelle (ein Auftrag kann in mehreren Portalen erscheinen)."""

    __tablename__ = "lead_sources"
    __table_args__ = (UniqueConstraint("source", "source_item_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(64))
    source_item_id: Mapped[str] = mapped_column(String(255))
    source_url: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)

    lead: Mapped[LeadRow] = relationship(back_populates="sources")


class LeadScoreDetailRow(Base):
    __tablename__ = "lead_score_details"
    __table_args__ = (UniqueConstraint("lead_id", "criterion"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    criterion: Mapped[str] = mapped_column(String(32))
    points: Mapped[int] = mapped_column(Integer)
    max_points: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)

    lead: Mapped[LeadRow] = relationship(back_populates="score_details")


class CrawlRunRow(Base):
    __tablename__ = "crawl_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(16))
    items_found: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    items_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    items_rejected: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    error_message: Mapped[str | None] = mapped_column(Text)


class ProcessedEmailRow(Base):
    """Bereits eingelesene E-Mail-Nachrichten (Deduplizierung über Läufe hinweg)."""

    __tablename__ = "processed_emails"

    message_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    source: Mapped[str | None] = mapped_column(String(64))
    subject: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    processed_at: Mapped[datetime] = mapped_column(UTCDateTime)


class FeedbackRow(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    relevant: Mapped[bool | None] = mapped_column(Boolean)
    rating: Mapped[int | None] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(Text)

    lead: Mapped[LeadRow] = relationship(back_populates="feedback")
