"""``report`` and ``history`` commands: read-only views of the local record."""

from rich.table import Table

from app.services.report_service import HistoryPoint, ReportService
from app.ui.commands import Runtime, resolve_or_report
from app.ui.timefmt import format_local


def run_report(runtime: Runtime) -> int:
    session = resolve_or_report(runtime)
    if session is None:
        return 2
    tz, console = runtime.settings.app_timezone, runtime.console
    report = ReportService(runtime.session_factory).session_report(
        session.slug, target_ids=frozenset(runtime.settings.target_product_ids)
    )

    console.print(f"REPORT {report.session_slug}", style="bold")
    monitor = report.monitor
    outcomes = ", ".join(f"{name} {count}" for name, count in sorted(monitor.outcomes.items()))
    console.print(f"Checks: {monitor.total_checks}" + (f" ({outcomes})" if outcomes else ""))
    console.print(f"First check: {format_local(monitor.first_check_at, tz)}")
    console.print(f"Last check: {format_local(monitor.last_check_at, tz)}")
    console.print(f"Last successful check: {format_local(monitor.last_success_at, tz)}")
    if monitor.average_ok_ms is not None:
        console.print(f"Average response time: {monitor.average_ok_ms} ms")

    for target in report.targets:
        console.print()
        console.print(f"TARGET {target.product_name} (#{target.product_id})", style="bold")
        if not target.points:
            console.print("No recorded stock changes.", style="dim")
            continue
        for at in target.restocks:
            console.print(f"Restocked at {format_local(at, tz)}", style="green")
        latest = target.points[-1]
        console.print(f"Latest recorded stock: {latest.remaining} at {format_local(latest.at, tz)}")

    console.print()
    table = Table(title="BOOKINGS", title_justify="left")
    for column in ("ID", "CUSTOMER", "PRODUCT", "QTY", "STATUS", "REFERENCE"):
        table.add_column(column, no_wrap=column != "CUSTOMER")
    for row in report.bookings:
        table.add_row(
            str(row.booking_id),
            row.customer_name,
            row.product_name,
            str(row.quantity),
            row.status,
            row.confirmation_ref or "-",
        )
    console.print(table)
    counts = ", ".join(f"{status} {n}" for status, n in sorted(report.bookings_by_status.items()))
    console.print(f"By status: {counts or 'none'}")
    console.print(
        "Bookings are local tracking records, not proof of PULN reservations.", style="dim"
    )
    return 0


def _print_points(runtime: Runtime, title: str, points: list[HistoryPoint]) -> int:
    console = runtime.console
    console.print(title, style="bold")
    if not points:
        console.print("No recorded changes for this session.")
        return 0
    for point in points:
        console.print(
            f"  {format_local(point.at, runtime.settings.app_timezone)}  {point.remaining}"
        )
    return 0


def run_history(runtime: Runtime, kind: str, subject_id: int) -> int:
    session = resolve_or_report(runtime)
    if session is None:
        return 2
    service = ReportService(runtime.session_factory)
    if kind == "stock":
        title = f"Stock remaining for product {subject_id} ({session.slug}), changes only"
        return _print_points(runtime, title, service.stock_history(session.slug, subject_id))
    title = f"Capacity remaining for slot {subject_id} ({session.slug}), changes only"
    return _print_points(runtime, title, service.slot_history(session.slug, subject_id))
