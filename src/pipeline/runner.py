"""Zentrale Pipeline-Orchestrierung.

Source -> Normalize -> Basic Dedup -> Prefilter -> LLM Analysis -> Validate -> Score -> SQLite

Die Komponenten sind nur über ihre Schnittstellen verbunden (``SourceConnector``,
``LeadAnalyzer``, ``Prefilter``, ``ScoreEngine``, Repositories). Grundsätze:

- Jeder Kandidat wird in einer eigenen Transaktion gespeichert; ein Fehler betrifft nur ihn.
- Bekannte Leads (Quelle + Source-ID oder URL) werden vor dem LLM erkannt (keine Kosten).
- Höchstens ``max_llm_analyses_per_run`` LLM-Analysen je Lauf; überzählige Kandidaten und
  Kandidaten mit vorübergehendem LLM-Fehler werden als ``pending_analysis`` gespeichert und in
  späteren Läufen zuerst analysiert.
- Eine Nachricht wird erst bestätigt (``acknowledge``), wenn alle ihre Leads gespeichert sind.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from src.config import PipelineSettings
from src.domain.enums import CrawlRunStatus, LeadClass, ProcessingStatus
from src.domain.models import CrawlRun, Lead, LeadCandidate, RawSourceItem, merge_source_facts
from src.llm.service import AnalysisFailedError, AnalysisResult
from src.pipeline.normalize import NormalizationError, normalize
from src.pipeline.prefilter import Prefilter
from src.scoring.engine import ScoreEngine
from src.sources.base import FetchResult, SourceConnector, safe_fetch
from src.storage.database import session_scope
from src.storage.repository import CrawlRunRepository, DuplicateLeadError, LeadRepository

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]


class LeadAnalyzer(Protocol):
    def analyze(self, candidate: LeadCandidate) -> AnalysisResult: ...


class RunSummary(BaseModel):
    run_id: int
    status: CrawlRunStatus
    started_at: datetime
    finished_at: datetime
    stats: dict[str, int]
    warnings: list[str]
    errors: list[str]
    lead_ids: list[int]


class _RunState:
    def __init__(self, run_id: int, llm_budget: int) -> None:
        self.run_id = run_id
        self.llm_budget = llm_budget
        self.stats: Counter[str] = Counter()
        self.warnings: list[str] = []
        self.errors: list[str] = []
        self.lead_ids: list[int] = []


class Pipeline:
    def __init__(
        self,
        connectors: Sequence[SourceConnector],
        analyzer: LeadAnalyzer,
        prefilter: Prefilter,
        score_engine: ScoreEngine,
        session_factory: sessionmaker[Session],
        settings: PipelineSettings,
        clock: Clock = lambda: datetime.now(UTC),
        acknowledge: bool = True,
    ) -> None:
        self._connectors = list(connectors)
        self._analyzer = analyzer
        self._prefilter = prefilter
        self._engine = score_engine
        self._session_factory = session_factory
        self._settings = settings
        self._clock = clock
        self._acknowledge = acknowledge

    @property
    def connector_names(self) -> list[str]:
        return [c.name for c in self._connectors]

    # --- Lauf ---------------------------------------------------------------

    def run(self, source: str | None = None) -> RunSummary:
        connectors = [c for c in self._connectors if source is None or c.name == source]
        if source is not None and not connectors:
            raise ValueError(f"Unbekannte Quelle '{source}'. Verfügbar: {', '.join(self.connector_names)}")

        started = self._clock()
        with session_scope(self._session_factory) as session:
            run_id = CrawlRunRepository(session).add(CrawlRun(source=source, started_at=started)).id
        assert run_id is not None
        state = _RunState(run_id, self._settings.max_llm_analyses_per_run)
        logger.info("crawl_started", extra={"run_id": run_id, "source": source})

        self._process_pending(state)
        for connector in connectors:
            self._process_connector(connector, state)

        return self._finish(state, started, source)

    def _process_pending(self, state: _RunState) -> None:
        """Ausstehende Analysen früherer Läufe zuerst (älteste zuerst, im Rahmen des LLM-Limits)."""
        if state.llm_budget <= 0:
            return
        with session_scope(self._session_factory) as session:
            pending = LeadRepository(session).list_pending(limit=state.llm_budget)
        for lead in pending:
            assert lead.id is not None
            state.stats["pending_resumed"] += 1
            updated = self._analyze(lead.to_candidate(), state, failures=lead.analysis_failures)
            updated.processed_run_id = state.run_id
            try:
                with session_scope(self._session_factory) as session:
                    LeadRepository(session).replace(lead.id, updated)
                state.lead_ids.append(lead.id)
            except SQLAlchemyError as exc:
                self._persistence_error(state, lead.to_candidate(), exc)

    def _process_connector(self, connector: SourceConnector, state: _RunState) -> None:
        result = safe_fetch(connector)
        self._count_fetch(result, state)
        state.warnings.extend(f"{connector.name}: {w}" for w in result.warnings)
        if not result.ok:
            state.errors.append(f"Quelle {connector.name}: {result.error}")
            return

        failed_keys: set[str] = set()
        unkeyed_failure = False
        seen_in_run: set[tuple[str, str]] = set()
        for item in result.items:
            if not self._process_item(item, state, seen_in_run):
                key = item.metadata.get("message_key")
                if key:
                    failed_keys.add(key)
                else:
                    unkeyed_failure = True

        if not self._acknowledge:
            return
        if unkeyed_failure:
            state.warnings.append(f"{connector.name}: Einträge ohne Nachrichtenbezug fehlgeschlagen, keine Bestätigung")
            return
        refs = [ref for ref in result.processed_refs if ref not in failed_keys]
        state.stats["messages_not_acknowledged"] += len(result.processed_refs) - len(refs)
        try:
            connector.acknowledge(result.model_copy(update={"processed_refs": refs}))
        except Exception as exc:  # noqa: BLE001 - Nachrichten werden dann erneut gelesen; Dedup verhindert Doppelarbeit
            state.warnings.append(f"{connector.name}: Bestätigung fehlgeschlagen ({type(exc).__name__}: {exc})")

    # --- Kandidat -----------------------------------------------------------

    def _process_item(self, item: RawSourceItem, state: _RunState, seen_in_run: set[tuple[str, str]]) -> bool:
        """Verarbeitet einen Eintrag. False = nicht gespeichert, Nachricht darf nicht bestätigt werden."""
        state.stats["items_found"] += 1
        try:
            candidate = normalize(item, self._clock())
        except (NormalizationError, ValidationError) as exc:
            state.stats["normalization_errors"] += 1
            state.warnings.append(f"Normalisierung fehlgeschlagen ({item.source}/{item.source_item_id}): {exc}")
            return True  # dauerhaft unbrauchbar; erneutes Lesen würde nicht helfen

        ref = (candidate.source, candidate.source_id)
        if ref in seen_in_run:  # bereits in diesem Lauf verarbeitet: nur Sichtung vermerken, nie erneut analysieren
            state.stats["duplicates"] += 1
            try:
                self._record_if_known(candidate, candidate.first_seen_at)
            except (SQLAlchemyError, DuplicateLeadError) as exc:
                state.warnings.append(f"Sichtung nicht vermerkt ({candidate.source}/{candidate.source_id}): {type(exc).__name__}")
            return True
        seen_in_run.add(ref)

        try:
            if self._record_if_known(candidate, candidate.first_seen_at):  # gleiche Zeitbasis wie Erstsichtung
                state.stats["duplicates"] += 1
                return True
        except (SQLAlchemyError, DuplicateLeadError) as exc:
            self._persistence_error(state, candidate, exc)
            return False

        state.stats["candidates_new"] += 1
        prefilter = self._prefilter.check(candidate)
        if prefilter.rejected:
            state.stats["prefiltered"] += 1
            lead = Lead.prefiltered(candidate, prefilter.reasons)
        elif state.llm_budget > 0:
            lead = self._analyze(candidate, state, failures=0)
        else:
            state.stats["deferred"] += 1
            lead = Lead.pending(candidate, error="LLM-Limit des Laufs erreicht")
        lead.processed_run_id = state.run_id

        try:
            with session_scope(self._session_factory) as session:
                saved = LeadRepository(session).add(lead)
        except (SQLAlchemyError, DuplicateLeadError) as exc:
            self._persistence_error(state, candidate, exc)
            return False
        assert saved.id is not None
        state.lead_ids.append(saved.id)
        state.stats["stored"] += 1
        return True

    def _record_if_known(self, candidate: LeadCandidate, seen_at: datetime) -> bool:
        """Basic Dedup: bekannte Quelle+ID oder URL -> nur Sichtung vermerken, nicht analysieren."""
        with session_scope(self._session_factory) as session:
            repo = LeadRepository(session)
            existing = repo.get_by_source_ref(candidate.source, candidate.source_id)
            if existing is None and candidate.source_url:
                existing = repo.find_by_url(candidate.source_url)
            if existing is None:
                return False
            assert existing.id is not None
            repo.record_source(existing.id, candidate.source, candidate.source_id, seen_at, candidate.source_url)
            return True

    def _analyze(self, candidate: LeadCandidate, state: _RunState, failures: int) -> Lead:
        state.llm_budget -= 1
        state.stats["llm_analyses"] += 1
        try:
            result = self._analyzer.analyze(candidate)
        except Exception as exc:  # noqa: BLE001 - Kandidat darf nicht verloren gehen
            reason = exc.reason if isinstance(exc, AnalysisFailedError) else type(exc).__name__
            failures += 1
            state.stats["llm_failures"] += 1
            state.warnings.append(f"LLM-Analyse fehlgeschlagen ({candidate.source}/{candidate.source_id}): {reason}")
            lead = Lead.pending(candidate, error=f"{reason}: {exc}", failures=failures)
            if failures >= self._settings.max_analysis_failures:
                lead.processing_status = ProcessingStatus.ANALYSIS_FAILED
            return lead

        if result.usage:
            state.stats["llm_input_tokens"] += result.usage.input_tokens or 0
            state.stats["llm_output_tokens"] += result.usage.output_tokens or 0
            state.stats["llm_cost_microusd"] += round((result.usage.cost_usd or 0) * 1_000_000)
        if result.grounding_issues:
            state.stats["grounding_issues"] += len(result.grounding_issues)
        merged = merge_source_facts(candidate, result.analysis)
        return Lead.build(candidate, merged, self._engine.score(merged),
                          prompt_version=result.prompt_version, llm_model=result.model)

    # --- Abschluss ----------------------------------------------------------

    @staticmethod
    def _count_fetch(result: FetchResult, state: _RunState) -> None:
        state.stats["mails_read"] += result.stats.messages_read
        state.stats["mail_duplicates"] += result.stats.duplicates
        state.stats["unknown_senders"] += result.stats.unknown_sender
        state.stats["mails_failed"] += result.stats.failed

    def _persistence_error(self, state: _RunState, candidate: LeadCandidate, exc: Exception) -> None:
        state.stats["persistence_errors"] += 1
        state.errors.append(f"Speichern fehlgeschlagen ({candidate.source}/{candidate.source_id}): {type(exc).__name__}")
        logger.error("lead_persist_failed", extra={"source": candidate.source, "source_id": candidate.source_id,
                                                   "error": str(exc)})

    def _finish(self, state: _RunState, started: datetime, source: str | None) -> RunSummary:
        with session_scope(self._session_factory) as session:
            leads = LeadRepository(session).list_for_run(state.run_id)
            state.stats["pending_total"] = LeadRepository(session).count_pending()
        for lead in leads:
            key = f"class_{lead.lead_class.value.lower()}" if lead.lead_class else f"status_{lead.processing_status.value}"
            state.stats[key] += 1

        processed_anything = state.stats["stored"] or state.stats["pending_resumed"] or state.stats["mails_read"]
        if not state.errors:
            status = CrawlRunStatus.SUCCESS
        elif processed_anything:
            status = CrawlRunStatus.PARTIAL
        else:
            status = CrawlRunStatus.FAILED
        finished = self._clock()

        stats = dict(state.stats)
        run = CrawlRun(
            id=state.run_id,
            source=source,
            started_at=started,
            finished_at=finished,
            status=status,
            items_found=stats.get("items_found", 0),
            items_new=stats.get("candidates_new", 0),
            items_analyzed=stats.get("llm_analyses", 0),
            items_rejected=stats.get("prefiltered", 0) + stats.get("class_reject", 0),
            warnings=state.warnings,
            error_message="\n".join(state.errors) or None,
            stats=stats,
        )
        with session_scope(self._session_factory) as session:
            CrawlRunRepository(session).update(run)
        logger.info("crawl_finished", extra={"run_id": state.run_id, "status": status.value, **stats})
        return RunSummary(
            run_id=state.run_id,
            status=status,
            started_at=started,
            finished_at=finished,
            stats=stats,
            warnings=state.warnings,
            errors=state.errors,
            lead_ids=state.lead_ids,
        )
