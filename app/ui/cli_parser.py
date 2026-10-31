"""Argument parser for ``python -m app.main``. One builder per command group."""

import argparse

from app.constants import APP_NAME, APP_VERSION
from app.ui.booking_commands import MARK_TARGETS

type Groups = argparse._SubParsersAction[argparse.ArgumentParser]


def _actions(groups: Groups, name: str, text: str) -> Groups:
    return groups.add_parser(name, help=text).add_subparsers(
        dest="action", required=True, metavar="ACTION"
    )


def _add_monitoring(groups: Groups) -> None:
    monitor = groups.add_parser("monitor", help="Monitor the session live until it closes")
    monitor.add_argument(
        "--keep-running",
        action="store_true",
        help="Keep polling slowly after the session stops accepting orders",
    )
    monitor.add_argument("--no-bell", action="store_true", help="Do not ring the terminal bell")
    monitor.add_argument(
        "--desktop", action="store_true", help="Show macOS notifications for alerts"
    )
    monitor.add_argument("--sound", action="store_true", help="Play a macOS sound for alerts")
    monitor.add_argument(
        "--auto-open",
        action="store_true",
        help="Open the official page for READY bookings when the session opens or a target "
        "is restocked (it never fills or submits anything)",
    )
    groups.add_parser("status", help="Refresh once and print the dashboard")
    sessions = _actions(groups, "sessions", "Session checks")
    sessions.add_parser("validate", help="Check the session page and live endpoint once")


def _add_products(groups: Groups) -> None:
    products = _actions(groups, "products", "Local product catalogue")
    products.add_parser("list", help="List known products and targets")
    name = products.add_parser("name", help="Set the display name of a product ID")
    name.add_argument("product_id", type=int)
    name.add_argument("name")
    limit = products.add_parser(
        "limit", help="Set the per-person limit seen on the official page (or none)"
    )
    limit.add_argument("product_id", type=int)
    limit.add_argument("max_quantity", metavar="N|none")


def _add_customers(groups: Groups) -> None:
    customers = _actions(groups, "customers", "Customer profiles (max 10 active)")
    listing = customers.add_parser("list", help="List customers with masked contacts")
    listing.add_argument("--active", action="store_true", help="Only active customers")
    customers.add_parser("add", help="Add a consenting customer (interactive)")
    show = customers.add_parser("show", help="Show one customer")
    show.add_argument("customer_id", type=int)
    show.add_argument("--reveal", action="store_true", help="Show contact details unmasked")
    edit = customers.add_parser("edit", help="Edit a customer (interactive)")
    edit.add_argument("customer_id", type=int)
    for action, text in (("activate", "Reactivate"), ("deactivate", "Deactivate")):
        customers.add_parser(action, help=f"{text} a customer").add_argument(
            "customer_id", type=int
        )
    delete = customers.add_parser("delete", help="Delete or anonymize a customer")
    delete.add_argument("customer_id", type=int)
    delete.add_argument(
        "--anonymize", action="store_true", help="Erase personal data but keep booking history"
    )
    delete.add_argument("--yes", action="store_true", help="Skip the typed confirmation")


def _add_bookings(groups: Groups) -> None:
    bookings = _actions(groups, "bookings", "Reservation preparation and tracking")
    listing = bookings.add_parser("list", help="Bookings for the configured session")
    listing.add_argument("--all", action="store_true", help="Include every session")
    bookings.add_parser("prepare", help="Prepare a booking for a customer (interactive)")
    for action, text in (
        ("show", "Show a booking and its history"),
        ("confirm", "Mark CONFIRMED after verifying the confirmation email"),
    ):
        bookings.add_parser(action, help=text).add_argument("booking_id", type=int)
    which = bookings.add_parser(
        "open", help="Open the official PULN page for a READY booking"
    ).add_mutually_exclusive_group(required=True)
    which.add_argument("booking_id", type=int, nargs="?")
    which.add_argument(
        "--all-ready", action="store_true", help="One browser tab per READY booking of the session"
    )
    review = bookings.add_parser(
        "review", help="Review a saved result page (.html) for a submitted booking"
    )
    review.add_argument("booking_id", type=int)
    review.add_argument("file", help="HTML file saved from the official result page")
    mark = bookings.add_parser("mark", help="Record a manual status change")
    mark.add_argument("booking_id", type=int)
    mark.add_argument("status", choices=sorted(MARK_TARGETS))
    mark.add_argument("--note", action="store_true", help="Add a note (asked interactively)")


def _add_reports(groups: Groups) -> None:
    groups.add_parser("report", help="Summary of the configured session")
    history = groups.add_parser("history", help="Recorded stock or slot changes")
    kinds = history.add_subparsers(dest="action", required=True, metavar="KIND")
    for kind, text in (("stock", "Stock changes of a product"), ("slot", "Capacity of a slot")):
        kinds.add_parser(kind, help=text).add_argument("subject_id", type=int, metavar="ID")
    exports = groups.add_parser("export", help="Write CSV files to data/exports")
    kinds = exports.add_subparsers(dest="action", required=True, metavar="KIND")
    bookings = kinds.add_parser("bookings", help="Bookings without contact details or notes")
    bookings.add_argument("--all", action="store_true", help="Include every session")
    bookings.add_argument(
        "--with-names", action="store_true", help="Include customer display names"
    )
    kinds.add_parser("stock", help="Recorded stock changes of the session")
    kinds.add_parser("slots", help="Recorded slot capacity changes of the session")
    db = _actions(groups, "db", "Local database maintenance")
    db.add_parser("init", help="Create the local SQLite database and tables")
    db.add_parser("backup", help="Copy the database to data/backups")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.main",
        description=f"{APP_NAME}: monitors PULN order availability. It never submits orders.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    groups = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for add in (_add_monitoring, _add_products, _add_customers, _add_bookings, _add_reports):
        add(groups)
    return parser
