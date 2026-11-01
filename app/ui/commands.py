"""CLI command handlers. Each handler returns a process exit code."""

import logging
import random
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass

import httpx
from rich.console import Console
from rich.live import Live
from rich.table import Table
from sqlalchemy import Engine, inspect
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.backend import PulnReadOnlyBackendAdapter
from app.clients.puln_client import LiveOk, PulnClient, create_http_client
from app.clock import utc_now
from app.config import Settings
from app.database.engine import sqlite_file_path
from app.repositories import booking_repository, customer_repository, product_repository, session_product_repository
from app.services.booking_service import BookingService
from app.services.browser_service import AutoOpener, OpenOutcome
from app.services.monitor_service import MonitorService, MonitorState, run_monitor
from app.services.notification_service import Notifier
from app.services.order_orchestrator import OrderOrchestrator
from app.services.poll_policy import PollTiming
from app.services.session_service import SessionConfigError, SessionRef, resolve_session
from app.services.stock_monitor import ChangeEvent, describe_result
from app.timefmt import format_local
from app.ui.dashboard import DashboardContext, render_dashboard

logger = logging.getLogger(__name__)

STALE_AFTER_INTERVALS = 3


@dataclass(frozen=True)
class Runtime:
    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]
    console: Console
    error_console: Console
    transport: httpx.BaseTransport | None = None
    open_url: Callable[[str], bool] = webbrowser.open

    def http_client(self) -> httpx.Client:
        return create_http_client(self.settings.puln_request_timeout_seconds, self.transport)


def run_db_init(runtime: Runtime) -> int:
    location = sqlite_file_path(runtime.settings.database_url) or "in-memory"
    table_count = len(inspect(runtime.engine).get_table_names())
    runtime.console.print(f"Database ready: {location} ({table_count} tables)", soft_wrap=True)
    return 0


def resolve_or_report(runtime: Runtime) -> SessionRef | None:
    try:
        return resolve_session(runtime.settings)
    except SessionConfigError as error:
        runtime.error_console.print(str(error), markup=False, soft_wrap=True)
        return None


def _dashboard_context(runtime: Runtime, target_ids: frozenset[int]) -> DashboardContext:
    settings = runtime.settings
    with runtime.session_factory() as db:
        active = customer_repository.count_active(db)
        prepared = booking_repository.count_prepared(db)
        confirmed = booking_repository.count_confirmed(db)
    return DashboardContext(
        timezone=settings.app_timezone,
        target_ids=target_ids,
        stale_after_seconds=settings.puln_poll_interval_seconds * STALE_AFTER_INTERVALS,
        max_customers=settings.max_customers,
        active_customers=active,
        prepared_bookings=prepared,
        confirmed_bookings=confirmed,
    )


def _session_target_ids(runtime: Runtime, session_slug: str) -> frozenset[int]:
    with runtime.session_factory() as db:
        mapped = session_product_repository.list_for_session(db, session_slug)
    if mapped:
        return frozenset(p.puln_product_id for p in mapped if p.variant_key == "kataifi-rolls")
    return frozenset(runtime.settings.target_product_ids)


def _monitor_service(
    runtime: Runtime,
    session: SessionRef,
    http: httpx.Client,
    *,
    keep_running: bool,
    notifier: Notifier | None,
    auto_opener: AutoOpener | None = None,
    track_orders: bool = False,
) -> MonitorService:
    settings = runtime.settings

    previous_outcomes: dict[int, str] = {}
    orchestrator = (
        OrderOrchestrator(
            session=session,
            backend=PulnReadOnlyBackendAdapter(PulnClient(http)),
            bookings=BookingService(runtime.session_factory, session_slug=session.slug),
        )
        if track_orders
        else None
    )

    def on_live(result: LiveOk) -> None:
        if orchestrator is None:
            return
        refresh = orchestrator.enqueue_ready_bookings()
        if refresh.rejected_ids:
            runtime.console.print(
                f"Order queue full; not queued: {', '.join(map(str, refresh.rejected_ids))}",
                style="yellow",
                markup=False,
            )
        for item in orchestrator.evaluate_live_status(result.status):
            outcome = item.outcome.value
            if previous_outcomes.get(item.booking_id) == outcome:
                continue
            previous_outcomes[item.booking_id] = outcome
            style = "bold green" if outcome == "READY_FOR_OPERATOR" else "yellow"
            runtime.console.print(
                f"Booking #{item.booking_id}: {outcome} — {item.detail}",
                style=style,
                markup=False,
                soft_wrap=True,
            )

    def on_event(event: ChangeEvent) -> None:
        if notifier is not None:
            notifier.notify(event)
        if auto_opener is not None and (outcome := auto_opener.handle(event)) is not None:
            _report_auto_open(runtime.console, outcome, session.page_url)

    return MonitorService(
        client=PulnClient(http),
        session_factory=runtime.session_factory,
        session=session,
        target_ids=_session_target_ids(runtime, session.slug),
        timing=PollTiming(
            interval=settings.puln_poll_interval_seconds,
            max_interval=settings.puln_max_poll_interval_seconds,
        ),
        keep_running=keep_running,
        clock=utc_now,
        rng=random.Random(),
        on_event=on_event,
        on_live=on_live if track_orders else None,
    )


def _report_auto_open(console: Console, outcome: OpenOutcome, url: str) -> None:
    if outcome.browser_failed:
        console.print(
            f"Auto-open: could not open a browser. Open this page yourself: {url}",
            style="bold red",
            markup=False,
            soft_wrap=True,
        )
    elif outcome.booking_ids:
        ids = ", ".join(map(str, outcome.booking_ids))
        console.print(
            f"Auto-open: official page opened for bookings {ids}. "
            "Complete each form and security check yourself.",
            style="bold yellow",
            markup=False,
        )
    else:
        console.print(
            "Auto-open: official page opened (no READY bookings).",
            style="bold yellow",
        )


def run_status(runtime: Runtime) -> int:
    """Manual refresh: exactly one request, then print the dashboard."""
    session = resolve_or_report(runtime)
    if session is None:
        return 2
    with runtime.http_client() as http:
        service = _monitor_service(runtime, session, http, keep_running=True, notifier=None)
        service.load_cached()
        service.poll_once()
    ctx = _dashboard_context(runtime, service.target_ids)
    runtime.console.print(render_dashboard(service.state, ctx, utc_now()))
    return 0 if isinstance(service.state.last_result, LiveOk) else 1


def run_monitor_command(
    runtime: Runtime,
    *,
    keep_running: bool,
    bell: bool,
    desktop: bool,
    sound: bool,
    auto_open: bool = False,
) -> int:
    session = resolve_or_report(runtime)
    if session is None:
        return 2
    console = runtime.console
    settings = runtime.settings
    notifier = Notifier(
        console,
        bell=bell and settings.notify_bell,
        desktop=desktop or settings.notify_desktop,
        sound=sound or settings.notify_sound,
    )
    auto_opener = (
        AutoOpener(
            BookingService(runtime.session_factory, session_slug=session.slug),
            session.page_url,
            runtime.open_url,
            target_ids=_session_target_ids(runtime, session.slug),
            clock=utc_now,
        )
        if auto_open
        else None
    )
    with runtime.http_client() as http:
        service = _monitor_service(
            runtime,
            session,
            http,
            keep_running=keep_running,
            notifier=notifier,
            auto_opener=auto_opener,
            track_orders=True,
        )
        service.load_cached()
        ctx = _dashboard_context(runtime, service.target_ids)
        live = Live(
            render_dashboard(service.state, ctx, utc_now()),
            console=console,
            transient=True,
            auto_refresh=False,
        )

        def render(state: MonitorState) -> None:
            live.update(render_dashboard(state, ctx, utc_now()), refresh=True)

        logger.info("monitoring %s", session.slug)
        interrupted = False
        try:
            with live:
                reason = run_monitor(service, sleep=time.sleep, on_update=render)
        except KeyboardInterrupt:
            interrupted, reason = True, "stopped by operator"
            logger.info("monitoring stopped by operator")
    console.print(render_dashboard(service.state, ctx, utc_now()))
    console.print(f"Monitoring ended: {reason}", markup=False, soft_wrap=True)
    if interrupted or isinstance(service.state.last_result, LiveOk):
        return 0
    return 1


def run_sessions_validate(runtime: Runtime) -> int:
    session = resolve_or_report(runtime)
    if session is None:
        return 2
    console, tz = runtime.console, runtime.settings.app_timezone
    with runtime.http_client() as http:
        client = PulnClient(http)
        page = client.check_page(session.page_url)
        live = client.fetch_live(session.live_url)

    console.print(f"Session: {session.slug}", soft_wrap=True)
    console.print(f"Page: {session.page_url}", soft_wrap=True)
    console.print(f"  {'OK' if page.reachable else 'FAILED'}: {page.detail}", markup=False)
    console.print(f"Live endpoint: {session.live_url}", soft_wrap=True)
    if isinstance(live, LiveOk):
        status = live.status
        console.print(
            f"  OK: valid JSON, HTTP {live.http_status} in {live.duration_ms} ms, "
            f"{len(status.products)} products, {len(status.slots)} slots",
            markup=False,
        )
        console.print(
            f"  Status: {status.status}, accepting: {'YES' if status.accepting else 'NO'}"
        )
        console.print(f"  Opens: {format_local(status.opens_at, tz)}")
        console.print(f"  Closes: {format_local(status.closes_at, tz)}")
        console.print(f"  Server time: {format_local(status.server_now, tz)}")
        if live.unknown_fields:
            fields = ", ".join(sorted(live.unknown_fields))
            console.print(f"  Note: unexpected fields {fields}", markup=False)
    else:
        detail = getattr(live, "reason", None)
        suffix = f": {detail}" if detail else ""
        console.print(f"  FAILED: {describe_result(live)}{suffix}", markup=False, soft_wrap=True)
    return 0 if page.reachable and isinstance(live, LiveOk) else 1


def run_products_sync(runtime: Runtime) -> int:
    """Read the current session's official HTML catalogue into a session-scoped map."""
    session = resolve_or_report(runtime)
    if session is None:
        return 2
    try:
        with runtime.http_client() as http:
            products = PulnClient(http).fetch_catalogue(session.page_url)
        with runtime.session_factory() as db:
            session_product_repository.upsert_catalogue(db, session.slug, products)
            db.commit()
    except (RuntimeError, ValueError) as error:
        runtime.error_console.print(f"Catalogue sync failed: {error}", markup=False)
        return 1
    table = Table(title=f"SESSION CATALOGUE ({session.slug})", title_justify="left")
    for column in ("ID", "VARIANT", "NAME", "PRICE", "STOCK", "MAX QTY"):
        table.add_column(column)
    for product in products:
        table.add_row(str(product.puln_product_id), product.variant_key, product.name,
            "-" if product.price is None else f"Rp {product.price:,.0f}",
            "-" if product.stock_remaining is None else str(product.stock_remaining),
            "unknown" if product.max_quantity is None else str(product.max_quantity))
    runtime.console.print(table)
    target = next((p for p in products if p.variant_key == "kataifi-rolls"), None)
    if target is None:
        runtime.error_console.print("Target variant kataifi-rolls was not found in this session.", markup=False)
        return 1
    runtime.console.print(
        f"Target resolved: Kataifi Rolls -> {target.name} (Product ID {target.puln_product_id})",
        markup=False)
    return 0


def run_products_list(runtime: Runtime) -> int:
    session = resolve_or_report(runtime)
    session_products = []
    if session is not None:
        with runtime.session_factory() as db:
            session_products = session_product_repository.list_for_session(db, session.slug)
    table = Table(title="SESSION PRODUCT CATALOGUE", title_justify="left")
    for column in ("ID", "VARIANT", "NAME", "MAX QTY", "PRICE"):
        table.add_column(column)
    for product in session_products:
        table.add_row(
            str(product.puln_product_id),
            product.variant_key,
            product.name,
            "unknown" if product.max_quantity is None else str(product.max_quantity),
            "-" if product.price is None else f"Rp {product.price}",
        )
    runtime.console.print(table)
    if not session_products:
        runtime.console.print("No session catalogue. Run: products sync", style="yellow")
    with runtime.session_factory() as db:
        legacy = product_repository.list_products(db)
    if legacy:
        legacy_table = Table(title="LEGACY / MANUAL CATALOGUE", title_justify="left")
        legacy_table.add_column("ID", justify="right")
        legacy_table.add_column("NAME")
        legacy_table.add_column("MAX QTY", justify="right")
        for product in legacy:
            legacy_table.add_row(
                str(product.puln_product_id),
                product.name,
                "-" if product.max_quantity is None else str(product.max_quantity),
            )
        runtime.console.print(legacy_table)
    return 0

def run_products_name(runtime: Runtime, product_id: int, name: str) -> int:
    with runtime.session_factory() as db:
        try:
            product = product_repository.set_product_name(db, product_id, name)
        except ValueError as error:
            runtime.error_console.print(str(error), markup=False)
            return 2
        db.commit()
    runtime.console.print(
        f"Product {product.puln_product_id} is now named {product.name}", markup=False
    )
    return 0


def run_products_limit(runtime: Runtime, product_id: int, limit: str) -> int:
    try:
        max_quantity = None if limit.strip().lower() == "none" else int(limit)
    except ValueError:
        runtime.error_console.print("limit must be a whole number or none", markup=False)
        return 2
    with runtime.session_factory() as db:
        try:
            product = product_repository.set_max_quantity(db, product_id, max_quantity)
        except ValueError as error:
            runtime.error_console.print(str(error), markup=False)
            return 2
        db.commit()
    shown = "no local limit" if product.max_quantity is None else f"max {product.max_quantity}"
    runtime.console.print(f"Product {product_id}: {shown}", markup=False)
    return 0
