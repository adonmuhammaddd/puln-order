"""Command-line entry point: ``python -m app.main --help``."""

import argparse
import logging
import sys
import webbrowser
from collections.abc import Callable, Sequence
from pathlib import Path

import httpx
from pydantic import ValidationError
from rich.console import Console

from app.config import Settings, load_settings
from app.database.engine import create_session_factory, init_db, sqlite_file_path
from app.logging_setup import configure_logging
from app.ui import booking_commands, commands, customer_commands, order_commands, report_commands
from app.ui.cli_parser import build_parser

logger = logging.getLogger(__name__)


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


def _dispatch_bookings(args: argparse.Namespace, runtime: commands.Runtime) -> int:
    match args.action:
        case "list":
            return booking_commands.run_list(runtime, all_sessions=args.all)
        case "prepare":
            return booking_commands.run_prepare(runtime)
        case "show":
            return booking_commands.run_show(runtime, args.booking_id)
        case "open" if args.all_ready:
            return booking_commands.run_open_all_ready(runtime)
        case "open":
            return booking_commands.run_open(runtime, args.booking_id)
        case "mark":
            return booking_commands.run_mark(
                runtime, args.booking_id, args.status, with_note=args.note
            )
        case "confirm":
            return booking_commands.run_confirm(runtime, args.booking_id)
        case "review":
            return booking_commands.run_review(runtime, args.booking_id, args.file)
    raise AssertionError(f"unhandled bookings action {args.action}")


def _dispatch_products(args: argparse.Namespace, runtime: commands.Runtime) -> int:
    match args.action:
        case "list":
            return commands.run_products_list(runtime)
        case "name":
            return commands.run_products_name(runtime, args.product_id, args.name)
        case "limit":
            return commands.run_products_limit(runtime, args.product_id, args.max_quantity)
    raise AssertionError(f"unhandled products action {args.action}")


def _dispatch_db(args: argparse.Namespace, runtime: commands.Runtime) -> int:
    if args.action == "backup":
        return report_commands.run_db_backup(runtime)
    return commands.run_db_init(runtime)


def _dispatch(args: argparse.Namespace, runtime: commands.Runtime) -> int:
    match args.command:
        case "monitor":
            return commands.run_monitor_command(
                runtime,
                keep_running=args.keep_running,
                bell=not args.no_bell,
                desktop=args.desktop,
                sound=args.sound,
                auto_open=args.auto_open,
            )
        case "orders":
            if args.action == "evaluate":
                return order_commands.run_evaluate(runtime, open_next=args.open_next)
            raise AssertionError(f"unhandled orders action {args.action}")
        case "status" | "sessions":
            handler = (
                commands.run_status
                if args.command == "status"
                else commands.run_sessions_validate
            )
            return handler(runtime)
        case "products":
            return _dispatch_products(args, runtime)
        case "customers":
            return _dispatch_customers(args, runtime)
        case "bookings":
            return _dispatch_bookings(args, runtime)
        case "report":
            return report_commands.run_report(runtime)
        case "history":
            return report_commands.run_history(runtime, args.action, args.subject_id)
        case "export":
            return report_commands.run_export(
                runtime,
                args.action,
                all_sessions=getattr(args, "all", False),
                with_names=getattr(args, "with_names", False),
            )
        case "db":
            return _dispatch_db(args, runtime)
    raise AssertionError(f"unhandled command {args.command}")


def main(
    argv: Sequence[str] | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
    open_url: Callable[[str], bool] | None = None,
) -> int:
    """Run the CLI. Tests replace the network and the browser; production passes nothing."""
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
        open_url=open_url or webbrowser.open,
    )
    try:
        return _dispatch(args, runtime)
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
