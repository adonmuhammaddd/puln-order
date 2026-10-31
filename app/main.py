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
from app.ui import commands, customer_commands

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

    customers = groups.add_parser("customers", help="Customer profiles (max 10 active)")
    customer_actions = customers.add_subparsers(dest="action", required=True, metavar="ACTION")
    listing = customer_actions.add_parser("list", help="List customers with masked contacts")
    listing.add_argument("--active", action="store_true", help="Only active customers")
    customer_actions.add_parser("add", help="Add a consenting customer (interactive)")
    show = customer_actions.add_parser("show", help="Show one customer")
    show.add_argument("customer_id", type=int)
    show.add_argument("--reveal", action="store_true", help="Show contact details unmasked")
    edit = customer_actions.add_parser("edit", help="Edit a customer (interactive)")
    edit.add_argument("customer_id", type=int)
    for action, text in (("activate", "Reactivate"), ("deactivate", "Deactivate")):
        toggle = customer_actions.add_parser(action, help=f"{text} a customer")
        toggle.add_argument("customer_id", type=int)
    delete = customer_actions.add_parser("delete", help="Delete or anonymize a customer")
    delete.add_argument("customer_id", type=int)
    delete.add_argument(
        "--anonymize",
        action="store_true",
        help="Erase personal data but keep booking history",
    )
    delete.add_argument("--yes", action="store_true", help="Skip the typed confirmation")

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


def _dispatch_customers(args: argparse.Namespace, runtime: commands.Runtime) -> int:
    match args.action:
        case "list":
            return customer_commands.run_list(runtime, active_only=args.active)
        case "add":
            return customer_commands.run_add(runtime)
        case "show":
            return customer_commands.run_show(runtime, args.customer_id, reveal=args.reveal)
        case "edit":
            return customer_commands.run_edit(runtime, args.customer_id)
        case "activate" | "deactivate":
            return customer_commands.run_set_active(
                runtime, args.customer_id, active=args.action == "activate"
            )
        case "delete":
            return customer_commands.run_delete(
                runtime, args.customer_id, anonymize=args.anonymize, assume_yes=args.yes
            )
    raise AssertionError(f"unhandled customers action {args.action}")


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
        case ("customers", _):
            return _dispatch_customers(args, runtime)
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
