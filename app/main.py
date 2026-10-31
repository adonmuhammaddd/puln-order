"""Command-line entry point: ``python -m app.main --help``."""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError
from rich.console import Console

from app.config import Settings, load_settings
from app.constants import APP_NAME, APP_VERSION
from app.database.engine import init_db, sqlite_file_path
from app.logging_setup import configure_logging
from app.ui import commands

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.main",
        description=f"{APP_NAME}: monitors PULN order availability. It never submits orders.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    groups = parser.add_subparsers(dest="group", required=True, metavar="COMMAND")

    db = groups.add_parser("db", help="Local database maintenance")
    db_commands = db.add_subparsers(dest="command", required=True, metavar="ACTION")
    db_commands.add_parser("init", help="Create the local SQLite database and tables")
    return parser


def _log_dir(settings: Settings) -> Path:
    db_file = sqlite_file_path(settings.database_url)
    return (db_file.parent if db_file else Path("data")) / "logs"


def _format_config_error(error: ValidationError) -> str:
    lines = ["Invalid configuration:"]
    for item in error.errors():
        field = ".".join(str(part) for part in item["loc"]).upper() or "SETTINGS"
        lines.append(f"  {field}: {item['msg']}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console()
    error_console = Console(stderr=True)

    try:
        settings = load_settings()
    except ValidationError as error:
        error_console.print(_format_config_error(error), soft_wrap=True, markup=False)
        return 2

    configure_logging(_log_dir(settings), settings.log_level)
    engine = init_db(settings.database_url)
    logger.info("started command %s %s", args.group, args.command)
    try:
        return commands.run_db_init(settings, engine, console)
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
