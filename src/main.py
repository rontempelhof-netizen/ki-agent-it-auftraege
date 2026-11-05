"""CLI-Einstiegspunkt: ``python -m src.main <command>``."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from src import __version__
from src.config import ConfigError, Settings, load_settings
from src.domain.enums import CrawlRunStatus
from src.logging_setup import configure_logging
from src.storage.database import SchemaMismatchError, create_db_engine, create_session_factory, init_db
from src.sources.base import SourceError

logger = logging.getLogger("src.main")

EXIT_OK, EXIT_PARTIAL, EXIT_ERROR = 0, 1, 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.main",
        description="KI-Agent für IT-Aufträge",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="Pfad zur YAML-Konfiguration (Standard: config/settings.yaml bzw. AGENT_CONFIG_FILE)",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    subparsers.add_parser("show-config", help="Wirksame Konfiguration als JSON ausgeben")
    subparsers.add_parser("init-db", help="SQLite-Datenbank initialisieren (idempotent)")
    subparsers.add_parser("gmail-auth", help="Einmalige lokale Gmail-OAuth-Freigabe (nur lesen)")
    subparsers.add_parser("gmail-check", help="Gmail-Label und Lesezugriff prüfen, ohne Änderungen")
    dup = subparsers.add_parser("duplicate-check", help="Plattformübergreifende Dublettenverdachte prüfen (nur lesen)")
    dup.add_argument("--limit", type=int, default=20, metavar="N", help="Maximal angezeigte Titelgruppen (Standard: 20)")

    pending = subparsers.add_parser("pending-leads", help="Ausstehende Projekte ohne LLM-Kosten anzeigen")
    pending.add_argument("--search", metavar="TEXT", help="Suchtext in Titel oder Beschreibung")
    pending.add_argument("--limit", type=int, default=20, metavar="N", help="Maximal 100 Einträge (Standard: 20)")

    review = subparsers.add_parser("review-leads", help="Fachlich interessante Rejects mit Datenlücken prüfen (nur lesen)")
    review.add_argument("--id", type=int, metavar="ID", help="Nur diese Lead-ID prüfen")
    review.add_argument("--limit", type=int, default=20, metavar="N", help="Maximal 100 Treffer (Standard: 20)")

    selected = subparsers.add_parser("analyze-lead", help="Nur einen gewählten Pending-Lead analysieren (kein Gmail-Abruf)")
    selected.add_argument("--id", type=int, required=True, metavar="ID", help="Lead-ID aus pending-leads")
    selected.add_argument("--fake-llm", action="store_true", help="Simulation statt kostenpflichtigem API-Aufruf")

    crawl = subparsers.add_parser("crawl", help="Quellen lesen, analysieren, bewerten, speichern, Report erzeugen")
    crawl.add_argument("--source", help="Nur diese Quelle (Connector-Name, z. B. email)")
    crawl.add_argument("--fake-llm", action="store_true", help="Fake-LLM statt echtem Provider (keine API-Kosten)")
    crawl.add_argument("--no-ack", action="store_true",
                       help="Nachrichten nicht als verarbeitet markieren (erneut lesbar, z. B. für Tests)")
    crawl.add_argument("--max-llm", type=int, metavar="N", help="LLM-Analysen in diesem Lauf begrenzen")
    crawl.add_argument("--no-report", action="store_true", help="Keinen HTML-Report erzeugen")
    crawl.add_argument("--send", action="store_true", help="Report per Mail versenden (gemäß mail.mode, Standard dry_run)")

    report = subparsers.add_parser("report", help="HTML-Report eines Laufs erzeugen")
    report.add_argument("--run-id", type=int, help="Lauf-ID (Standard: letzter Lauf)")
    report.add_argument("--output", type=Path, help="Zieldatei (Standard: report.output_dir/report-run-<id>.html)")
    report.add_argument("--send", action="store_true", help="Report per Mail versenden (gemäß mail.mode)")

    test_mail = subparsers.add_parser("test-mail", help="Test-Mail senden (gemäß mail.mode, Standard dry_run)")
    test_mail.add_argument("--to", action="append", metavar="ADDR", help="Empfänger statt mail.recipients")
    return parser


def _cmd_show_config(settings: Settings, _args: argparse.Namespace) -> int:
    print(settings.model_dump_json(indent=2))
    return EXIT_OK


def _prepare_db(settings: Settings):
    engine = create_db_engine(settings.database)
    init_db(engine)
    return engine, create_session_factory(engine)


def _cmd_init_db(settings: Settings, _args: argparse.Namespace) -> int:
    engine = create_db_engine(settings.database)
    try:
        version = init_db(engine)
    finally:
        engine.dispose()
    print(f"Datenbank initialisiert: {settings.database.url} (Schema-Version {version})")
    return EXIT_OK


def _print_delivery(delivery) -> None:  # type: ignore[no-untyped-def]
    if delivery.mode == "dry_run":
        print(f"Mail (Dry-Run) abgelegt: {delivery.path} – nichts versendet")
    else:
        print(f"Mail versendet an: {', '.join(delivery.recipients)}")


def _cmd_crawl(settings: Settings, args: argparse.Namespace) -> int:
    from src.app import build_pipeline, generate_report, send_report
    from src.llm import FakeLLMProvider, create_provider

    if args.max_llm is not None:
        settings = settings.model_copy(
            update={"pipeline": settings.pipeline.model_copy(update={"max_llm_analyses_per_run": args.max_llm})}
        )
    provider = (
        FakeLLMProvider.from_responses_file(settings.llm.fake_responses_file)
        if args.fake_llm and settings.llm.fake_responses_file
        else FakeLLMProvider() if args.fake_llm else create_provider(settings.llm)
    )
    engine, factory = _prepare_db(settings)
    try:
        pipeline = build_pipeline(settings, factory, provider=provider, acknowledge=not args.no_ack)
        summary = pipeline.run(source=args.source)
        s = summary.stats
        print(
            f"Lauf #{summary.run_id}: {summary.status.value} | Mails {s.get('mails_read', 0)} | "
            f"neu {s.get('candidates_new', 0)} | Duplikate {s.get('duplicates', 0)} | "
            f"Prefilter {s.get('prefiltered', 0)} | LLM {s.get('llm_analyses', 0)} | "
            f"A {s.get('class_a', 0)} B {s.get('class_b', 0)} C {s.get('class_c', 0)} "
            f"Reject {s.get('class_reject', 0)} | ausstehend {s.get('pending_total', 0)}"
        )
        for error in summary.errors:
            print(f"FEHLER: {error}", file=sys.stderr)
        if not args.no_report:
            output = generate_report(settings, factory, run_id=summary.run_id)
            print(f"Report: {output.html_path}")
            if args.send:
                _print_delivery(send_report(settings, output.report))
    finally:
        engine.dispose()
    return {CrawlRunStatus.SUCCESS: EXIT_OK, CrawlRunStatus.PARTIAL: EXIT_PARTIAL}.get(summary.status, EXIT_ERROR)


def _cmd_report(settings: Settings, args: argparse.Namespace) -> int:
    from src.app import ReportNotFoundError, generate_report, send_report

    engine, factory = _prepare_db(settings)
    try:
        output = generate_report(settings, factory, run_id=args.run_id, output=args.output)
    except ReportNotFoundError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        engine.dispose()
    print(f"Report: {output.html_path}")
    if args.send:
        _print_delivery(send_report(settings, output.report))
    return EXIT_OK


def _cmd_test_mail(settings: Settings, args: argparse.Namespace) -> int:
    from src.app import send_test_mail
    from src.notify.mailer import MailError

    try:
        _print_delivery(send_test_mail(settings, recipients=args.to))
    except MailError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


def _one_line(value: str | None, max_chars: int = 140) -> str:
    """Verhindert Zeilenumbrüche und Steuerzeichen aus fremden Ausschreibungstexten."""
    clean = " ".join("".join(char if char.isprintable() else " " for char in (value or "")).split())
    return clean[:max_chars] + ("…" if len(clean) > max_chars else "")


def _cmd_pending_leads(settings: Settings, args: argparse.Namespace) -> int:
    from src.storage.repository import LeadRepository

    if not 1 <= args.limit <= 100:
        print("Fehler: --limit muss zwischen 1 und 100 liegen.", file=sys.stderr)
        return EXIT_ERROR
    search = (args.search or "").strip()
    if args.search is not None and not search:
        print("Fehler: --search darf nicht leer sein.", file=sys.stderr)
        return EXIT_ERROR
    engine, factory = _prepare_db(settings)
    try:
        with factory() as session:
            leads = LeadRepository(session).list_pending(limit=args.limit, search=search or None)
    finally:
        engine.dispose()
    if not leads:
        print("Keine passenden ausstehenden Projekte gefunden.")
        return EXIT_OK
    for lead in leads:
        print(f"Lead #{lead.id} | {lead.source} | {_one_line(lead.title, 110)}")
        if lead.description:
            print(f"  {_one_line(lead.description, 180)}")
    print(f"{len(leads)} ausstehende Projekte angezeigt (nur lesen, keine API-Anfrage).")
    return EXIT_OK


def _cmd_review_leads(settings: Settings, args: argparse.Namespace) -> int:
    """Berechnet die zweite Sichtung aus gespeicherten Analysen, ohne Daten zu ändern."""
    from src.scoring.review_triage import review_advice
    from src.storage.repository import LeadRepository

    if not 1 <= args.limit <= 100:
        print("Fehler: --limit muss zwischen 1 und 100 liegen.", file=sys.stderr)
        return EXIT_ERROR
    if args.id is not None and args.id < 1:
        print("Fehler: --id muss positiv sein.", file=sys.stderr)
        return EXIT_ERROR

    engine, factory = _prepare_db(settings)
    try:
        with factory() as session:
            repo = LeadRepository(session)
            if args.id is not None:
                lead = repo.get(args.id)
                if lead is None:
                    print(f"Fehler: Lead #{args.id} nicht gefunden.", file=sys.stderr)
                    return EXIT_ERROR
                candidates = [lead]
            else:
                candidates = repo.list_leads()
        matches = [
            (lead, advice)
            for lead in candidates
            if (advice := review_advice(lead)) is not None
        ]
    finally:
        engine.dispose()

    if not matches:
        print("Keine fachlich interessanten Rejects mit mindestens zwei Informationslücken gefunden.")
        print("Keine Daten geändert oder API-Anfragen ausgeführt.")
        return EXIT_OK
    print(f"{len(matches)} Klärfall/Klärfälle gefunden; zeige maximal {args.limit}.")
    for lead, advice in matches[:args.limit]:
        print(f"Lead #{lead.id} | {lead.lead_class.value} {lead.score_total}/100 "
              f"| {lead.source} | {_one_line(lead.title, 150)}")
        if lead.fit_reason:
            print(f"  Passung: {_one_line(lead.fit_reason, 350)}")
        for reason in advice.reasons:
            print(f"  Prüfen: {reason}")
        if lead.source_url:
            print(f"  Link: {_one_line(lead.source_url, 300)}")
    print("Nur Lesemodus; keine Neubewertung, keine Gmail- oder Claude-Anfrage.")
    return EXIT_OK


def _cmd_analyze_lead(settings: Settings, args: argparse.Namespace) -> int:
    from src.app import build_pipeline
    from src.domain.enums import ProcessingStatus
    from src.llm import FakeLLMProvider, create_provider
    from src.storage.repository import LeadRepository

    if args.id < 1:
        print("Fehler: --id muss eine positive ganze Zahl sein.", file=sys.stderr)
        return EXIT_ERROR
    settings = settings.model_copy(
        update={"pipeline": settings.pipeline.model_copy(update={"max_llm_analyses_per_run": 1})}
    )
    engine, factory = _prepare_db(settings)
    try:
        with factory() as session:
            existing = LeadRepository(session).get(args.id)
        if existing is None:
            print(f"Fehler: Lead #{args.id} existiert nicht.", file=sys.stderr)
            return EXIT_ERROR
        if existing.processing_status != ProcessingStatus.PENDING_ANALYSIS:
            print(f"Fehler: Lead #{args.id} ist nicht ausstehend ({existing.processing_status.value}).", file=sys.stderr)
            return EXIT_ERROR
        provider = FakeLLMProvider() if args.fake_llm else create_provider(settings.llm)
        pipeline = build_pipeline(settings, factory, provider=provider, connectors=[], acknowledge=False)
        summary = pipeline.run_selected(args.id)
        with factory() as session:
            lead = LeadRepository(session).get(args.id)
        assert lead is not None
        print(f"Lead #{args.id}: {_one_line(lead.title)}")
        print(f"Status: {lead.processing_status.value} | Klasse: {lead.lead_class.value if lead.lead_class else '-'} "
              f"| Score: {lead.score_total if lead.lead_class else '-'}")
        if lead.summary:
            print(f"Zusammenfassung: {_one_line(lead.summary, 1000)}")
        if lead.fit_reason:
            print(f"Passung: {_one_line(lead.fit_reason, 1000)}")
        if lead.hard_fail_reasons:
            print("Ausschlüsse: " + "; ".join(_one_line(reason, 160) for reason in lead.hard_fail_reasons))
        if lead.processing_error:
            print(f"Analysefehler: {_one_line(lead.processing_error, 300)}")
        cost = summary.stats.get("llm_cost_microusd", 0) / 1_000_000
        print(f"Lauf #{summary.run_id}: LLM {summary.stats.get('llm_analyses', 0)} | "
              f"Fehler {summary.stats.get('llm_failures', 0)} | geschätzte API-Kosten {cost:.4f} USD")
        print("Keine E-Mails gelesen oder versendet; keine anderen Leads analysiert.")
        return EXIT_PARTIAL if summary.stats.get("llm_failures") or summary.errors else EXIT_OK
    finally:
        engine.dispose()


def _cmd_duplicate_check(settings: Settings, args: argparse.Namespace) -> int:
    """Findet exakt gleiche normalisierte Titel auf unterschiedlichen Plattformen.

    Ausschließlich Bericht: keine Zusammenführung und keine Veränderung der Leads.
    """
    from sqlalchemy import select

    from src.pipeline.duplicate_review import DuplicateEntry, find_cross_source_title_matches
    from src.storage.orm import LeadRow

    if args.limit < 1:
        print("Fehler: --limit muss mindestens 1 sein.", file=sys.stderr)
        return EXIT_ERROR

    engine = create_db_engine(settings.database)
    try:
        factory = create_session_factory(engine)
        with factory() as session:
            entries = [
                DuplicateEntry(lead_id, source, source_id, title, url)
                for lead_id, source, source_id, title, url in session.execute(
                    select(LeadRow.id, LeadRow.source, LeadRow.source_id, LeadRow.title, LeadRow.source_url)
                )
            ]
    finally:
        engine.dispose()

    groups = find_cross_source_title_matches(entries)
    print(f"Dublettenverdacht: {len(groups)} Titelgruppen auf unterschiedlichen Plattformen (nur lesen).")
    for group in groups[:args.limit]:
        evidence = (
            "Gemeinsame Kennung im Titel: " + ", ".join(group.reference_hints)
            if group.reference_hints
            else "Gleicher Titel, Identität ungeklärt"
        )
        print(f"\n{group.title} | {len(group.entries)} Einträge | {evidence}")
        for entry in group.entries:
            print(f"  Lead #{entry.lead_id}: {entry.source} / {entry.source_id} | {entry.source_url or 'kein Link'}")
    if len(groups) > args.limit:
        print(f"\nWeitere {len(groups) - args.limit} Gruppen nicht angezeigt (--limit erhöhen).")
    print("Keine Einträge verändert oder zusammengeführt.")
    return EXIT_OK


def _cmd_gmail_auth(settings: Settings, _args: argparse.Namespace) -> int:
    from src.sources.email.gmail_mailbox import authorize_gmail

    authorize_gmail(settings.sources.email.gmail)
    print("Gmail-Freigabe gespeichert (Nur-Lese-Berechtigung).")
    return EXIT_OK


def _cmd_gmail_check(settings: Settings, _args: argparse.Namespace) -> int:
    from src.sources.email.gmail_mailbox import GmailMailbox

    mailbox = GmailMailbox(settings.sources.email.gmail)
    message = next(mailbox.iter_messages(), None)
    if message is None:
        print(f"Gmail-Label '{settings.sources.email.gmail.label}' erreichbar, keine Nachrichten gefunden.")
    else:
        print(
            f"Gmail-Label '{settings.sources.email.gmail.label}' erreichbar; "
            f"erste Nachricht lesbar ({len(message.raw)} Bytes, ID {message.ref})."
        )
    print("Keine Nachrichten verändert.")
    return EXIT_OK


COMMANDS = {
    "show-config": _cmd_show_config,
    "init-db": _cmd_init_db,
    "gmail-auth": _cmd_gmail_auth,
    "gmail-check": _cmd_gmail_check,
    "duplicate-check": _cmd_duplicate_check,
    "pending-leads": _cmd_pending_leads,
    "review-leads": _cmd_review_leads,
    "analyze-lead": _cmd_analyze_lead,
    "crawl": _cmd_crawl,
    "report": _cmd_report,
    "test-mail": _cmd_test_mail,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return EXIT_OK

    try:
        settings = load_settings(config_file=args.config)
    except (ConfigError, ValidationError) as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        return EXIT_ERROR

    configure_logging(settings.logging)
    logger.info(
        "app_started",
        extra={"version": __version__, "environment": settings.environment, "command": args.command},
    )
    try:
        return COMMANDS[args.command](settings, args)
    except SchemaMismatchError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except SourceError as exc:
        print(f"Gmail-/Quellenfehler: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except ValueError as exc:  # z. B. unbekannte Quelle
        print(f"Fehler: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
