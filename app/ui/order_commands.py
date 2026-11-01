"""Order orchestration commands for read-only availability and operator handoff."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.table import Table

from app.adapters.backend import PulnReadOnlyBackendAdapter
from app.clients.puln_client import PulnClient
from app.models.booking import BookingStatus
from app.models.execution_result import ExecutionOutcome
from app.services.booking_service import BookingService
from app.services.order_orchestrator import OrderOrchestrator
from app.ui.commands import resolve_or_report

if TYPE_CHECKING:
    from app.ui.commands import Runtime


def run_evaluate(runtime: Runtime, *, open_next: bool = False) -> int:
    """Assess every READY booking against one fresh read-only PULN inspection.

    With open_next, hand off only the first eligible booking to the operator's normal browser.
    The command never fills or submits the official form.
    """
    session = resolve_or_report(runtime)
    if session is None:
        return 2

    bookings = BookingService(runtime.session_factory, session_slug=session.slug)
    with runtime.http_client() as http:
        orchestrator = OrderOrchestrator(
            session=session,
            backend=PulnReadOnlyBackendAdapter(PulnClient(http)),
            bookings=bookings,
        )
        refresh = orchestrator.enqueue_ready_bookings()
        results = orchestrator.evaluate_queue()

    console = runtime.console
    console.print(
        f"Queue: {len(refresh.enqueued_ids)} added, "
        f"{len(refresh.already_queued_ids)} already queued, "
        f"{len(refresh.rejected_ids)} rejected (limit {orchestrator.queue.max_pending}).",
        markup=False,
    )
    if not results:
        console.print(f"No READY bookings for session {session.slug}.", markup=False)
        return 0

    table = Table(title="ORDER AVAILABILITY ASSESSMENT", title_justify="left")
    table.add_column("BOOKING", justify="right")
    table.add_column("RESULT")
    table.add_column("STOCK", justify="right")
    table.add_column("SLOT", justify="right")
    for result in results:
        table.add_row(
            str(result.booking_id),
            result.outcome.value,
            "-" if result.stock_remaining is None else str(result.stock_remaining),
            "-" if result.slot_remaining is None else str(result.slot_remaining),
        )
    console.print(table)
    for result in results:
        console.print(f"  #{result.booking_id}: {result.detail}", markup=False, soft_wrap=True)

    if not open_next:
        return 0

    candidate = next(
        (r for r in results if r.outcome is ExecutionOutcome.READY_FOR_OPERATOR), None
    )
    if candidate is None:
        console.print("No booking is currently eligible for operator handoff.", style="yellow")
        return 1

    booking = bookings.get(candidate.booking_id)
    if booking.status is not BookingStatus.READY:
        console.print(
            f"Booking {booking.id} changed to {booking.status}; refresh the assessment.",
            style="yellow",
            markup=False,
        )
        return 1
    if not runtime.open_url(session.page_url):
        runtime.error_console.print(
            f"Could not open a browser. Open this page yourself: {session.page_url}",
            markup=False,
            soft_wrap=True,
        )
        return 1

    bookings.transition(booking.id, BookingStatus.OFFICIAL_FLOW_OPENED)
    console.print(
        f"Opened the official PULN page for booking {booking.id}. "
        "Complete the form and any security check yourself.",
        style="yellow",
        markup=False,
    )
    console.print(
        f"After completing the official flow, record it with: "
        f"bookings mark {booking.id} submitted",
        markup=False,
    )
    return 0
