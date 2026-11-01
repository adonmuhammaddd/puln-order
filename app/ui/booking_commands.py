"""``bookings`` commands. The app prepares and tracks; the official PULN page does the rest."""

from collections.abc import Callable
from pathlib import Path

from rich.table import Table

from app.database.tables import Booking
from app.models.booking import BookingStatus, can_transition
from app.models.customer import mask_email, mask_phone, normalize_notes
from app.parsers.reservation_response_parser import (
    ReservationReview,
    ResponseTooLargeError,
    ReviewOutcome,
    compare_with_booking,
    parse_reservation_response,
    read_response_file,
)
from app.repositories import (
    customer_repository,
    product_repository,
    session_product_repository,
    snapshot_repository,
)
from app.services.booking_service import BookingError, BookingService, PreparationRequest
from app.services.browser_service import open_ready_bookings
from app.services.session_service import resolve_session
from app.timefmt import format_local
from app.ui.commands import Runtime, resolve_or_report
from app.ui.prompts import PromptCancelled, TooManyAttempts, ask, ask_valid

MARK_TARGETS: dict[str, BookingStatus] = {
    "draft": BookingStatus.DRAFT,
    "ready": BookingStatus.READY,
    "submitted": BookingStatus.SUBMITTED_BY_USER,
    "success-page-reviewed": BookingStatus.SUCCESS_PAGE_REVIEWED,
    "awaiting-email": BookingStatus.AWAITING_EMAIL,
    "failed": BookingStatus.FAILED,
    "cancelled": BookingStatus.CANCELLED,
}

OFFICIAL_FLOW_NOTICE = (
    "Complete the order form and any security check yourself in the browser. "
    "This app does not fill or submit anything. Afterwards run: bookings mark {id} submitted"
)
NO_BROWSER = "Could not open a browser. Open this page yourself: {url}"


def _whole_number(text: str) -> int:
    try:
        return int(text.strip())
    except ValueError:
        raise ValueError("enter a whole number") from None


def _service(runtime: Runtime) -> BookingService | None:
    session = resolve_or_report(runtime)
    if session is None:
        return None
    return BookingService(runtime.session_factory, session_slug=session.slug)


def _guarded(runtime: Runtime, action: Callable[[BookingService], int]) -> int:
    service = _service(runtime)
    if service is None:
        return 2
    try:
        return action(service)
    except BookingError as error:
        runtime.error_console.print(str(error), markup=False, soft_wrap=True)
        return 1
    except PromptCancelled:
        runtime.console.print("Cancelled; nothing was changed.")
        return 1
    except TooManyAttempts as error:
        runtime.error_console.print(f"Too many invalid values for {error}; nothing was changed.")
        return 2


def _print_choices(runtime: Runtime, slug: str) -> None:
    console = runtime.console
    with runtime.session_factory() as db:
        product_repository.seed_known_products(db)
        db.commit()
        customers = customer_repository.list_customers(db, active_only=True)
        products = product_repository.list_products(db)
        session_products = session_product_repository.list_for_session(db, slug)
        cached = snapshot_repository.load_last_state(db, slug)
    console.print("Active customers:", style="bold")
    for customer in customers:
        console.print(f"  {customer.id}  {customer.display_name}", markup=False)
    console.print("Products (active session):", style="bold")
    for product in session_products:
        limit = "limit unknown" if product.max_quantity is None else f"max {product.max_quantity}"
        console.print(
            f"  {product.puln_product_id}  {product.name}, {limit} [{product.variant_key}]",
            markup=False,
        )
    if not session_products:
        console.print("  No session catalogue yet; run products sync.", style="yellow")
    console.print("Legacy products:", style="bold")
    for product in products:
        limit = "no local limit" if product.max_quantity is None else f"max {product.max_quantity}"
        console.print(f"  {product.puln_product_id}  {product.name}, {limit}", markup=False)
    if cached is None:
        console.print("Pickup slots: no live data yet (run status)", style="yellow")
        return
    status, observed_at = cached
    as_of = format_local(observed_at, runtime.settings.app_timezone)
    console.print(f"Pickup slots (last known, {as_of}):", style="bold")
    for slot in status.slots:
        console.print(f"  {slot.id}  {slot.label}  remaining {slot.remaining}", markup=False)


def run_prepare(runtime: Runtime) -> int:
    def action(service: BookingService) -> int:
        console = runtime.console
        _print_choices(runtime, service.session_slug)
        with runtime.session_factory() as db:
            session_products = session_product_repository.list_for_session(db, service.session_slug)
        target_product = next(
            (p for p in session_products if p.variant_key == "kataifi-rolls"), None
        )
        if target_product is None:
            console.print(
                "Target product is not mapped for this session. Run: products sync",
                style="yellow",
            )
            return 1
        default_product = target_product.puln_product_id
        customer_id = ask_valid(console, "Customer ID", _whole_number)
        product_id = ask_valid(
            console, f"Product ID [{default_product}]", _whole_number, keep_blank=True
        )
        quantity = ask_valid(console, "Quantity [1]", _whole_number, keep_blank=True)
        slot_id = ask_valid(
            console, "Pickup slot ID (Enter for none)", _whole_number, keep_blank=True
        )
        notes = ask_valid(console, "Notes (optional)", normalize_notes)
        assert customer_id is not None
        request = PreparationRequest(
            customer_id=customer_id,
            product_id=default_product if product_id is None else product_id,
            quantity=1 if quantity is None else quantity,
            slot_id=slot_id,
            notes=notes,
        )
        check = service.check(request)
        console.print(f"Product: {check.product_name} x {request.quantity}", markup=False)
        console.print(f"Pickup slot: {check.slot_label or '-'}", markup=False)
        if check.data_observed_at is not None:
            as_of = format_local(check.data_observed_at, runtime.settings.app_timezone)
            console.print(f"Last known data from {as_of}")
        for warning in check.warnings:
            console.print(f"  ! {warning}", style="yellow", markup=False)
        choice = ask(console, "Save as READY or DRAFT? Type READY or DRAFT (Enter cancels)")
        if choice.strip().upper() not in {"READY", "DRAFT"}:
            console.print("Not saved.")
            return 1
        booking = service.prepare(request, ready=choice.strip().upper() == "READY")
        console.print(f"Booking {booking.id} saved as {booking.status}.")
        return 0

    return _guarded(runtime, action)


def run_list(runtime: Runtime, *, all_sessions: bool) -> int:
    def action(service: BookingService) -> int:
        bookings = service.list_bookings(all_sessions=all_sessions)
        with runtime.session_factory() as db:
            legacy_names = product_repository.product_names(db)
            names_by_session = {
                booking.session_slug: {
                    p.puln_product_id: p.name
                    for p in session_product_repository.list_for_session(db, booking.session_slug)
                }
                for booking in bookings
            }
            customers = {c.id: c.display_name for c in customer_repository.list_customers(db)}
        table = Table(
            title=f"BOOKINGS ({'all sessions' if all_sessions else service.session_slug})",
            title_justify="left",
        )
        for column in ("ID", "CUSTOMER", "PRODUCT", "QTY", "SLOT", "STATUS"):
            table.add_column(column, no_wrap=column in {"STATUS", "SLOT", "QTY", "ID"})
        for booking in bookings:
            table.add_row(
                str(booking.id),
                customers.get(booking.customer_id, f"#{booking.customer_id}"),
                names_by_session.get(booking.session_slug, {}).get(
                    booking.puln_product_id,
                    legacy_names.get(
                        booking.puln_product_id, f"Product #{booking.puln_product_id}"
                    ),
                ),
                str(booking.quantity),
                "-" if booking.puln_slot_id is None else str(booking.puln_slot_id),
                booking.status,
            )
        runtime.console.print(table)
        return 0

    return _guarded(runtime, action)


WORKFLOW_NEXT: dict[BookingStatus, str] = {
    BookingStatus.DRAFT: "Review details; mark ready when prepared",
    BookingStatus.READY: "Run orders evaluate, then open the official page",
    BookingStatus.OFFICIAL_FLOW_OPENED: (
        "Complete the official form yourself; then mark submitted"
    ),
    BookingStatus.SUBMITTED_BY_USER: (
        "Review the saved result page, or mark awaiting-email"
    ),
    BookingStatus.SUCCESS_PAGE_REVIEWED: "Mark awaiting-email and wait for the email",
    BookingStatus.AWAITING_EMAIL: (
        "Verify the official confirmation email, then run bookings confirm"
    ),
    BookingStatus.CONFIRMED: "Complete",
    BookingStatus.FAILED: "Terminal — investigate before preparing a new booking",
    BookingStatus.CANCELLED: "Terminal — no further action",
}


def run_workflow(runtime: Runtime, *, all_sessions: bool) -> int:
    def action(service: BookingService) -> int:
        bookings = service.list_bookings(all_sessions=all_sessions)
        table = Table(
            title=f"BOOKING WORKFLOW ({'all sessions' if all_sessions else service.session_slug})",
            title_justify="left",
        )
        for column in ("ID", "SESSION", "CUSTOMER", "PRODUCT", "STATUS", "NEXT ACTION"):
            table.add_column(column, no_wrap=column in {"ID", "STATUS"})
        with runtime.session_factory() as db:
            names = product_repository.product_names(db)
            customers = {c.id: c.display_name for c in customer_repository.list_customers(db)}
        for booking in bookings:
            table.add_row(
                str(booking.id),
                booking.session_slug,
                customers.get(booking.customer_id, f"#{booking.customer_id}"),
                names.get(booking.puln_product_id, f"Product #{booking.puln_product_id}"),
                booking.status.value,
                WORKFLOW_NEXT[booking.status],
            )
        if not bookings:
            runtime.console.print("No bookings found for this session.")
            return 0
        runtime.console.print(table)
        runtime.console.print(
            "FIFO order is by booking ID (oldest first). Status comes from the local booking "
            "database; this view does not infer that PULN accepted an order.",
            style="dim",
            markup=False,
            soft_wrap=True,
        )
        return 0

    return _guarded(runtime, action)


def _describe(runtime: Runtime, booking: Booking) -> list[tuple[str, str]]:
    with runtime.session_factory() as db:
        customer = customer_repository.get(db, booking.customer_id)
        product = product_repository.get_product(db, booking.puln_product_id)
        slot = (
            None
            if booking.puln_slot_id is None
            else snapshot_repository.get_pickup_slot(db, booking.session_slug, booking.puln_slot_id)
        )
    customer_text = "-"
    if customer is not None:
        contact = f"{mask_phone(customer.whatsapp)}, {mask_email(customer.email)}"
        customer_text = f"{customer.display_name} ({contact})"
    slot_text = (
        "-"
        if booking.puln_slot_id is None
        else (f"{booking.puln_slot_id} {slot.label if slot else '(label unknown)'}")
    )
    return [
        ("Customer", f"{booking.customer_id} {customer_text}"),
        ("Session", booking.session_slug),
        ("Product", f"{booking.puln_product_id} {product.name if product else ''}".strip()),
        ("Quantity", str(booking.quantity)),
        ("Pickup slot", slot_text),
    ]


def run_show(runtime: Runtime, booking_id: int) -> int:
    def action(service: BookingService) -> int:
        booking = service.get(booking_id)
        tz, console = runtime.settings.app_timezone, runtime.console
        rows = [
            ("Booking", str(booking.id)),
            *_describe(runtime, booking),
            ("Status", str(booking.status)),
            ("Notes", booking.operator_notes or "-"),
            ("Confirmation reference", booking.confirmation_ref or "-"),
            ("Confirmed at", format_local(booking.confirmed_at, tz)),
            ("Created", format_local(booking.created_at, tz)),
        ]
        for label, value in rows:
            console.print(f"{label}: {value}", markup=False, soft_wrap=True)
        console.print("History:", style="bold")
        for change in service.history(booking_id):
            note = f"  ({change.note})" if change.note else ""
            source = change.from_status or "new"
            console.print(
                f"  {format_local(change.changed_at, tz)}  {source} -> {change.to_status}{note}",
                markup=False,
                soft_wrap=True,
            )
        console.print(
            "This is a local tracking record, not proof of a PULN reservation.", style="dim"
        )
        return 0

    return _guarded(runtime, action)


def _page_url(runtime: Runtime, slug: str) -> str:
    current = resolve_session(runtime.settings)
    if current.slug == slug:
        return current.page_url
    return f"{runtime.settings.puln_base_url}/c/{slug}"


def _warn_if_not_accepting(runtime: Runtime, slug: str) -> None:
    with runtime.session_factory() as db:
        cached = snapshot_repository.load_last_state(db, slug)
    if cached is not None and not cached[0].accepting:
        as_of = format_local(cached[1], runtime.settings.app_timezone)
        runtime.console.print(
            f"Warning: the session was not accepting orders at the last check ({as_of}).",
            style="yellow",
        )


def run_open(runtime: Runtime, booking_id: int) -> int:
    def action(service: BookingService) -> int:
        booking = service.get(booking_id)
        console = runtime.console
        if booking.status not in {BookingStatus.READY, BookingStatus.OFFICIAL_FLOW_OPENED}:
            runtime.error_console.print(
                f"booking {booking_id} is {booking.status}; only READY bookings can be opened",
                markup=False,
            )
            return 1
        for label, value in _describe(runtime, booking):
            console.print(f"{label}: {value}", markup=False, soft_wrap=True)
        console.print(f"Full contact details: customers show {booking.customer_id} --reveal")
        _warn_if_not_accepting(runtime, booking.session_slug)
        url = _page_url(runtime, booking.session_slug)
        if not runtime.open_url(url):
            console.print(NO_BROWSER.format(url=url), markup=False, soft_wrap=True)
            return 1
        if booking.status is BookingStatus.READY:
            booking = service.transition(booking_id, BookingStatus.OFFICIAL_FLOW_OPENED)
        console.print(f"Opened {url}", markup=False, soft_wrap=True)
        console.print(OFFICIAL_FLOW_NOTICE.format(id=booking_id), style="yellow", markup=False)
        console.print(f"Booking {booking_id} is {booking.status}.")
        return 0

    return _guarded(runtime, action)


def run_open_all_ready(runtime: Runtime) -> int:
    def action(service: BookingService) -> int:
        console, slug = runtime.console, service.session_slug
        _warn_if_not_accepting(runtime, slug)
        url = _page_url(runtime, slug)
        outcome = open_ready_bookings(service, url, runtime.open_url)
        for tab, booking_id in enumerate(outcome.booking_ids, start=1):
            details = ", ".join(value for _, value in _describe(runtime, service.get(booking_id)))
            console.print(
                f"Tab {tab}: booking {booking_id}: {details}", markup=False, soft_wrap=True
            )
        if outcome.browser_failed:
            console.print(NO_BROWSER.format(url=url), markup=False, soft_wrap=True)
            return 1
        if not outcome.booking_ids:
            console.print(f"No READY bookings for {slug}; nothing was opened.", markup=False)
            return 1
        console.print(f"Opened {url} in {len(outcome.booking_ids)} tab(s).", markup=False)
        console.print(OFFICIAL_FLOW_NOTICE.format(id="<id>"), style="yellow", markup=False)
        console.print("Full contact details: customers show <customer id> --reveal")
        return 0

    return _guarded(runtime, action)


def run_mark(runtime: Runtime, booking_id: int, target: str, *, with_note: bool) -> int:
    def action(service: BookingService) -> int:
        note = ask_valid(runtime.console, "Note", normalize_notes) if with_note else None
        booking = service.transition(booking_id, MARK_TARGETS[target], note=note)
        runtime.console.print(f"Booking {booking_id} is {booking.status}.")
        return 0

    return _guarded(runtime, action)


def run_confirm(runtime: Runtime, booking_id: int) -> int:
    def action(service: BookingService) -> int:
        console = runtime.console
        service.get(booking_id)
        answer = ask(
            console,
            f"Have you checked the official PULN confirmation email for booking {booking_id}? "
            "Type yes",
        )
        if answer.strip().lower() != "yes":
            console.print("Not confirmed; the booking stays as it is.")
            return 1
        reference = ask(console, "Reference shown in the email (optional)")
        booking = service.confirm(booking_id, email_verified=True, reference=reference)
        console.print(f"Booking {booking_id} is {booking.status}.")
        return 0

    return _guarded(runtime, action)


def _print_review(runtime: Runtime, review: ReservationReview) -> None:
    console = runtime.console
    style = {"SUCCESS": "bold green", "FAILURE": "bold red"}.get(review.outcome, "bold yellow")
    console.print(f"Outcome: {review.outcome}", style=style)
    for label, value in (
        ("Title", review.title),
        ("Heading", review.heading),
        ("Total", review.total),
        ("Pickup", review.pickup),
        ("Location", review.location),
        ("Visible reference", review.reference),
    ):
        console.print(f"{label}: {value or '-'}", markup=False, soft_wrap=True)
    console.print("Line items:")
    for item in review.line_items:
        quantity = "?" if item.quantity is None else str(item.quantity)
        console.print(f"  {item.name} x {quantity}  {item.amount or ''}".rstrip(), markup=False)
    if not review.line_items:
        console.print("  (none found)", style="dim")
    if review.mentions_email_code:
        console.print("The page says the reservation code is sent by email.")
    for reason in review.reasons:
        console.print(f"  ! {reason}", style="yellow", markup=False)


def run_review(runtime: Runtime, booking_id: int, file: str) -> int:
    def action(service: BookingService) -> int:
        console = runtime.console
        path = Path(file)
        try:
            review = parse_reservation_response(read_response_file(path))
        except (OSError, ResponseTooLargeError) as error:
            runtime.error_console.print(f"cannot read {file}: {error}", markup=False)
            return 2
        booking = service.get(booking_id)
        _print_review(runtime, review)

        with runtime.session_factory() as db:
            product = session_product_repository.get_by_id(
                db, booking.session_slug, booking.puln_product_id
            ) or product_repository.get_product(db, booking.puln_product_id)
            slot = (
                None
                if booking.puln_slot_id is None
                else snapshot_repository.get_pickup_slot(
                    db, booking.session_slug, booking.puln_slot_id
                )
            )
        warnings = []
        if product is not None:
            warnings += compare_with_booking(
                review, product_name=product.name, quantity=booking.quantity
            )
        if slot is not None and review.pickup and review.pickup != slot.label:
            warnings.append(
                f"The pickup on the page differs from the booking's slot ({slot.label})."
            )
        for warning in warnings:
            console.print(f"  ! {warning}", style="yellow", markup=False)
        console.print(
            "A success page is not a confirmation. The booking is confirmed only after the "
            "official confirmation email is checked (bookings confirm).",
            style="dim",
        )

        if review.outcome is ReviewOutcome.NEEDS_REVIEW:
            console.print("This page needs manual review; the booking was not changed.")
            return 1
        target = (
            BookingStatus.SUCCESS_PAGE_REVIEWED
            if review.outcome is ReviewOutcome.SUCCESS
            else BookingStatus.FAILED
        )
        if not can_transition(booking.status, target):
            runtime.error_console.print(
                f"booking {booking_id} is {booking.status}; it cannot move to {target}",
                markup=False,
            )
            return 1
        if (
            ask(console, f"Record this review and move booking {booking_id} to {target}? Type yes")
            .strip()
            .lower()
            != "yes"
        ):
            console.print("Not recorded; the booking stays as it is.")
            return 1
        booking = service.record_page_review(booking_id, review)
        console.print(f"Booking {booking_id} is {booking.status}.")
        if booking.status is BookingStatus.SUCCESS_PAGE_REVIEWED:
            console.print(f"Next: bookings mark {booking_id} awaiting-email")
        return 0

    return _guarded(runtime, action)
