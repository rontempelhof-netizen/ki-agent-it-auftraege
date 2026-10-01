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


COMMANDS = {
    "show-config": _cmd_show_config,
    "init-db": _cmd_init_db,
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
    except ValueError as exc:  # z. B. unbekannte Quelle
        print(f"Fehler: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
