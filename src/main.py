"""CLI-Einstiegspunkt: ``python -m src.main <command>``."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from pydantic import ValidationError

from src import __version__
from src.config import ConfigError, Settings, load_settings
from src.logging_setup import configure_logging
from src.storage.database import create_db_engine, init_db

logger = logging.getLogger("src.main")


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
    return parser


def _cmd_show_config(settings: Settings) -> int:
    print(settings.model_dump_json(indent=2))
    return 0


def _cmd_init_db(settings: Settings) -> int:
    engine = create_db_engine(settings.database)
    try:
        version = init_db(engine)
    finally:
        engine.dispose()
    print(f"Datenbank initialisiert: {settings.database.url} (Schema-Version {version})")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0

    try:
        settings = load_settings(config_file=args.config)
    except (ConfigError, ValidationError) as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        return 2

    configure_logging(settings.logging)
    logger.info(
        "app_started",
        extra={"version": __version__, "environment": settings.environment, "command": args.command},
    )

    commands = {
        "show-config": _cmd_show_config,
        "init-db": _cmd_init_db,
    }
    return commands[args.command](settings)


if __name__ == "__main__":
    sys.exit(main())
