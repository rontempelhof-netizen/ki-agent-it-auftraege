"""Repository-Schicht: übersetzt zwischen Pydantic-Domänenmodellen und ORM-Zeilen.

Repositories arbeiten auf einer übergebenen Session; Commit/Rollback verantwortet
der Aufrufer (z. B. über ``session_scope``).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from src.domain.enums import LeadClass, LeadStatus, ProcessingStatus
from src.domain.models import CrawlRun, Feedback, Lead, LeadAnalysis, LeadSourceRef, ScoreItem, ScoreResult
from src.domain.status import ensure_transition
from src.sources.email.seen_store import ProcessedMessage
from src.storage.orm import CrawlRunRow, FeedbackRow, LeadRow, LeadScoreDetailRow, LeadSourceRow, ProcessedEmailRow

_LEAD_COLUMN_FIELDS = frozenset(Lead.model_fields) - {"id", "score_breakdown", "analysis"}
_CRAWL_RUN_FIELDS = frozenset(CrawlRun.model_fields) - {"id"}


class LeadNotFoundError(LookupError):
    pass


class DuplicateLeadError(ValueError):
    pass


def _plain(value: Any) -> Any:
    return value.value if isinstance(value, StrEnum) else value


def _score_rows(items: Iterable[ScoreItem]) -> list[LeadScoreDetailRow]:
    return [
        LeadScoreDetailRow(criterion=item.criterion.value, points=item.points, max_points=item.max_points, reason=item.reason)
        for item in items
    ]


class LeadRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, lead: Lead) -> Lead:
        """Speichert einen neuen Lead inkl. Score-Details und primärem Quellvorkommen."""
        if self.get_by_source_ref(lead.source, lead.source_id) is not None:
            raise DuplicateLeadError(f"Lead existiert bereits: {lead.source}/{lead.source_id}")
        row = LeadRow(**{f: _plain(v) for f, v in lead.model_dump(include=_LEAD_COLUMN_FIELDS).items()})
        row.analysis = lead.analysis.model_dump(mode="json") if lead.analysis else None
        row.score_details = _score_rows(lead.score_breakdown)
        row.sources = [
            LeadSourceRow(
                source=lead.source,
                source_item_id=lead.source_id,
                source_url=lead.source_url,
                first_seen_at=lead.first_seen_at,
                last_seen_at=lead.first_seen_at,
            )
        ]
        self._session.add(row)
        self._session.flush()
        return self._to_domain(row)

    def get(self, lead_id: int) -> Lead | None:
        row = self._session.get(LeadRow, lead_id)
        return self._to_domain(row) if row else None

    def get_by_source_ref(self, source: str, source_item_id: str) -> Lead | None:
        """Findet einen Lead über ein beliebiges Quellvorkommen (primär oder weiteres)."""
        row = self._session.scalar(
            select(LeadRow)
            .join(LeadSourceRow)
            .where(LeadSourceRow.source == source, LeadSourceRow.source_item_id == source_item_id)
        )
        return self._to_domain(row) if row else None

    def find_by_url(self, url: str) -> Lead | None:
        """Findet einen Lead über die (kanonische) Projekt-URL eines beliebigen Vorkommens."""
        row = self._session.scalar(
            select(LeadRow)
            .outerjoin(LeadSourceRow)
            .where(or_(LeadRow.source_url == url, LeadSourceRow.source_url == url))
            .limit(1)
        )
        return self._to_domain(row) if row else None

    def list_pending(self, limit: int | None = None) -> list[Lead]:
        """Noch nicht analysierte Leads, älteste zuerst."""
        query = (
            select(LeadRow)
            .where(LeadRow.processing_status == ProcessingStatus.PENDING_ANALYSIS.value)
            .order_by(LeadRow.first_seen_at, LeadRow.id)
        )
        if limit is not None:
            query = query.limit(limit)
        return [self._to_domain(row) for row in self._session.scalars(query)]

    def count_pending(self) -> int:
        return len(self.list_pending())

    def list_for_run(self, run_id: int) -> list[Lead]:
        """Im angegebenen Lauf verarbeitete Leads, absteigend nach Score."""
        query = select(LeadRow).where(LeadRow.processed_run_id == run_id).order_by(LeadRow.score_total.desc(), LeadRow.id)
        return [self._to_domain(row) for row in self._session.scalars(query)]

    def replace(self, lead_id: int, lead: Lead) -> Lead:
        """Überschreibt Analyse-, Score- und Verarbeitungsdaten eines bestehenden Leads.

        Unverändert bleiben ID, Erst-/Letztsichtung, Vertriebsstatus und Quellvorkommen.
        """
        row = self._require(lead_id)
        keep = {"first_seen_at", "last_seen_at", "status"}
        for field, value in lead.model_dump(include=_LEAD_COLUMN_FIELDS - keep).items():
            setattr(row, field, _plain(value))
        row.analysis = lead.analysis.model_dump(mode="json") if lead.analysis else None
        row.score_details.clear()
        self._session.flush()  # alte Details vor dem Einfügen löschen (Unique-Constraint)
        row.score_details = _score_rows(lead.score_breakdown)
        self._session.flush()
        return self._to_domain(row)

    def list_leads(
        self,
        classes: Iterable[LeadClass] | None = None,
        statuses: Iterable[LeadStatus] | None = None,
        min_score: int | None = None,
        limit: int | None = None,
    ) -> list[Lead]:
        """Leads absteigend nach Score, bei Gleichstand nach ID."""
        query = select(LeadRow).order_by(LeadRow.score_total.desc(), LeadRow.id)
        if classes is not None:
            query = query.where(LeadRow.lead_class.in_([c.value for c in classes]))
        if statuses is not None:
            query = query.where(LeadRow.status.in_([s.value for s in statuses]))
        if min_score is not None:
            query = query.where(LeadRow.score_total >= min_score)
        if limit is not None:
            query = query.limit(limit)
        return [self._to_domain(row) for row in self._session.scalars(query)]

    def update_status(self, lead_id: int, new_status: LeadStatus) -> Lead:
        row = self._require(lead_id)
        ensure_transition(LeadStatus(row.status), new_status)
        row.status = new_status.value
        self._session.flush()
        return self._to_domain(row)

    def update_score(
        self,
        lead_id: int,
        score: ScoreResult,
        analysis: LeadAnalysis | None = None,
        prompt_version: str | None = None,
        llm_model: str | None = None,
    ) -> Lead:
        """Ersetzt Score, Klasse, Hard Fails und Score-Details (z. B. nach Neubewertung)."""
        row = self._require(lead_id)
        row.score_details.clear()
        self._session.flush()  # alte Details vor dem Einfügen löschen (Unique-Constraint)
        row.score_details = _score_rows(score.items)
        row.score_total = score.total
        row.lead_class = score.lead_class.value
        row.hard_fail = score.hard_fail
        row.hard_fail_reasons = [r.message for r in score.hard_fail_reasons]
        if analysis is not None:
            row.analysis = analysis.model_dump(mode="json")
        if prompt_version is not None:
            row.prompt_version = prompt_version
        if llm_model is not None:
            row.llm_model = llm_model
        self._session.flush()
        return self._to_domain(row)

    def record_source(
        self,
        lead_id: int,
        source: str,
        source_item_id: str,
        seen_at: datetime,
        source_url: str | None = None,
    ) -> LeadSourceRef:
        """Vermerkt ein (weiteres) Vorkommen; bekannte Vorkommen erhalten nur last_seen_at."""
        row = self._session.scalar(
            select(LeadSourceRow).where(LeadSourceRow.source == source, LeadSourceRow.source_item_id == source_item_id)
        )
        if row is not None and row.lead_id != lead_id:
            raise DuplicateLeadError(f"Quellvorkommen {source}/{source_item_id} gehört zu Lead {row.lead_id}")
        lead_row = self._require(lead_id)  # Referenz halten (Identity-Map hält Objekte nur schwach)
        if row is None:
            row = LeadSourceRow(
                source=source,
                source_item_id=source_item_id,
                source_url=source_url,
                first_seen_at=seen_at,
                last_seen_at=seen_at,
            )
            lead_row.sources.append(row)
        else:
            row.last_seen_at = max(row.last_seen_at, seen_at)
        if lead_row.last_seen_at is None or lead_row.last_seen_at < seen_at:
            lead_row.last_seen_at = seen_at
        self._session.flush()
        return LeadSourceRef.model_validate(row, from_attributes=True)

    def list_sources(self, lead_id: int) -> list[LeadSourceRef]:
        return [LeadSourceRef.model_validate(r, from_attributes=True) for r in self._require(lead_id).sources]

    def _require(self, lead_id: int) -> LeadRow:
        row = self._session.get(LeadRow, lead_id)
        if row is None:
            raise LeadNotFoundError(f"Lead {lead_id} nicht gefunden")
        return row

    @staticmethod
    def _to_domain(row: LeadRow) -> Lead:
        data: dict[str, Any] = {field: getattr(row, field) for field in _LEAD_COLUMN_FIELDS}
        data["id"] = row.id
        data["analysis"] = row.analysis
        data["score_breakdown"] = [
            {"criterion": d.criterion, "points": d.points, "max_points": d.max_points, "reason": d.reason}
            for d in row.score_details
        ]
        return Lead.model_validate(data)


class CrawlRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, run: CrawlRun) -> CrawlRun:
        row = CrawlRunRow(**{f: _plain(v) for f, v in run.model_dump(include=_CRAWL_RUN_FIELDS).items()})
        self._session.add(row)
        self._session.flush()
        return CrawlRun.model_validate(row, from_attributes=True)

    def update(self, run: CrawlRun) -> CrawlRun:
        if run.id is None or (row := self._session.get(CrawlRunRow, run.id)) is None:
            raise LookupError(f"CrawlRun {run.id} nicht gefunden")
        for field, value in run.model_dump(include=_CRAWL_RUN_FIELDS).items():
            setattr(row, field, _plain(value))
        self._session.flush()
        return CrawlRun.model_validate(row, from_attributes=True)

    def get(self, run_id: int) -> CrawlRun | None:
        row = self._session.get(CrawlRunRow, run_id)
        return CrawlRun.model_validate(row, from_attributes=True) if row else None

    def list_recent(self, limit: int = 10) -> list[CrawlRun]:
        rows = self._session.scalars(select(CrawlRunRow).order_by(CrawlRunRow.started_at.desc()).limit(limit))
        return [CrawlRun.model_validate(r, from_attributes=True) for r in rows]


class FeedbackRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, feedback: Feedback) -> Feedback:
        if self._session.get(LeadRow, feedback.lead_id) is None:
            raise LeadNotFoundError(f"Lead {feedback.lead_id} nicht gefunden")
        row = FeedbackRow(**feedback.model_dump(exclude={"id"}))
        self._session.add(row)
        self._session.flush()
        return Feedback.model_validate(row, from_attributes=True)

    def list_for_lead(self, lead_id: int) -> list[Feedback]:
        rows = self._session.scalars(select(FeedbackRow).where(FeedbackRow.lead_id == lead_id).order_by(FeedbackRow.id))
        return [Feedback.model_validate(r, from_attributes=True) for r in rows]


class ProcessedEmailStore:
    """SQLite-Implementierung von ``SeenMessageStore`` für den EmailSourceConnector."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def is_seen(self, key: str) -> bool:
        return self._session.get(ProcessedEmailRow, key) is not None

    def mark_seen(self, messages: Iterable[ProcessedMessage], processed_at: datetime) -> None:
        for message in messages:
            if self._session.get(ProcessedEmailRow, message.key) is None:
                self._session.add(
                    ProcessedEmailRow(
                        message_key=message.key,
                        status=message.status,
                        source=message.source,
                        subject=message.subject,
                        received_at=message.received_at,
                        item_count=message.item_count,
                        processed_at=processed_at,
                    )
                )
        self._session.flush()

    def get(self, key: str) -> ProcessedMessage | None:
        row = self._session.get(ProcessedEmailRow, key)
        if row is None:
            return None
        return ProcessedMessage(
            key=row.message_key,
            status=row.status,  # type: ignore[arg-type]
            source=row.source,
            subject=row.subject,
            received_at=row.received_at,
            item_count=row.item_count,
        )


class ScopedProcessedEmailStore:
    """``SeenMessageStore`` mit eigener kurzer Transaktion je Aufruf (für langlebige Connectoren)."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def is_seen(self, key: str) -> bool:
        with self._session_factory() as session:
            return ProcessedEmailStore(session).is_seen(key)

    def mark_seen(self, messages: Iterable[ProcessedMessage], processed_at: datetime) -> None:
        with self._session_factory() as session, session.begin():
            ProcessedEmailStore(session).mark_seen(messages, processed_at)
