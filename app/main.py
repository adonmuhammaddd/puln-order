"""Command-line entry point: ``python -m app.main --help``."""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

import httpx
from pydantic import ValidationError
from rich.console import Console

from app.config import Settings, load_settings
from app.constants import APP_NAME, APP_VERSION
from app.database.engine import create_session_factory, init_db, sqlite_file_path
from app.logging_setup import configure_logging
from app.ui import commands

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.main",
        description=f"{APP_NAME}: monitors PULN order availability. It never submits orders.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    groups = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    monitor = groups.add_parser("monitor", help="Monitor the session live until it closes")
    monitor.add_argument(
        "--keep-running",
        action="store_true",
        help="Keep polling slowly after the session stops accepting orders",
    )
    monitor.add_argument("--no-bell", action="store_true", help="Do not ring the terminal bell")

    groups.add_parser("status", help="Refresh once and print the dashboard")

    sessions = groups.add_parser("sessions", help="Session checks")
    session_actions = sessions.add_subparsers(dest="action", required=True, metavar="ACTION")
    session_actions.add_parser("validate", help="Check the session page and live endpoint once")

    products = groups.add_parser("products", help="Local product catalogue")
    product_actions = products.add_subparsers(dest="action", required=True, metavar="ACTION")
    product_actions.add_parser("list", help="List known products and targets")
    name = product_actions.add_parser("name", help="Set the display name of a product ID")
    name.add_argument("product_id", type=int)
    name.add_argument("name")

    db = groups.add_parser("db", help="Local database maintenance")
    db_actions = db.add_subparsers(dest="action", required=True, metavar="ACTION")
    db_actions.add_parser("init", help="Create the local SQLite database and tables")
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


def _dispatch(args: argparse.Namespace, runtime: commands.Runtime) -> int:
    match (args.command, getattr(args, "action", None)):
        case ("monitor", None):
            return commands.run_monitor_command(
                runtime, keep_running=args.keep_running, bell=not args.no_bell
            )
        case ("status", None):
            return commands.run_status(runtime)
        case ("sessions", "validate"):
            return commands.run_sessions_validate(runtime)
        case ("products", "list"):
            return commands.run_products_list(runtime)
        case ("products", "name"):
            return commands.run_products_name(runtime, args.product_id, args.name)
        case ("db", "init"):
            return commands.run_db_init(runtime)
    raise AssertionError(f"unhandled command {args.command} {args.action}")


def main(argv: Sequence[str] | None = None, *, transport: httpx.BaseTransport | None = None) -> int:
    """Run the CLI. ``transport`` lets tests replace the network; production passes nothing."""
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
    logger.info("started command %s %s", args.command, getattr(args, "action", ""))
    runtime = commands.Runtime(
        settings=settings,
        engine=engine,
        session_factory=create_session_factory(engine),
        console=console,
        error_console=error_console,
        transport=transport,
    )
    try:
        return _dispatch(args, runtime)
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
