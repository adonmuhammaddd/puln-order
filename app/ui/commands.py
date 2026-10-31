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

from app.clients.puln_client import LiveOk, PulnClient, create_http_client
from app.clock import utc_now
from app.config import Settings
from app.database.engine import sqlite_file_path
from app.repositories import booking_repository, customer_repository, product_repository
from app.services.monitor_service import MonitorService, MonitorState, run_monitor
from app.services.notification_service import TerminalNotifier
from app.services.poll_policy import PollTiming
from app.services.session_service import SessionConfigError, SessionRef, resolve_session
from app.services.stock_monitor import ChangeEvent, describe_result
from app.ui.dashboard import DashboardContext, render_dashboard
from app.ui.timefmt import format_local

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


def _monitor_service(
    runtime: Runtime,
    session: SessionRef,
    http: httpx.Client,
    *,
    keep_running: bool,
    notifier: TerminalNotifier | None,
) -> MonitorService:
    settings = runtime.settings

    def on_event(event: ChangeEvent) -> None:
        if notifier is not None:
            notifier.notify(event)

    return MonitorService(
        client=PulnClient(http),
        session_factory=runtime.session_factory,
        session=session,
        target_ids=frozenset(settings.target_product_ids),
        timing=PollTiming(
            interval=settings.puln_poll_interval_seconds,
            max_interval=settings.puln_max_poll_interval_seconds,
        ),
        keep_running=keep_running,
        clock=utc_now,
        rng=random.Random(),
        on_event=on_event,
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


def run_monitor_command(runtime: Runtime, *, keep_running: bool, bell: bool) -> int:
    session = resolve_or_report(runtime)
    if session is None:
        return 2
    console = runtime.console
    notifier = TerminalNotifier(console, bell=bell and runtime.settings.notify_bell)
    with runtime.http_client() as http:
        service = _monitor_service(
            runtime, session, http, keep_running=keep_running, notifier=notifier
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


def run_products_list(runtime: Runtime) -> int:
    targets = frozenset(runtime.settings.target_product_ids)
    with runtime.session_factory() as db:
        product_repository.seed_known_products(db)
        product_repository.sync_targets(db, targets)
        db.commit()
        products = product_repository.list_products(db)
    table = Table(title="PRODUCT CATALOGUE", title_justify="left")
    table.add_column("ID", justify="right")
    table.add_column("NAME")
    table.add_column("MAX QTY", justify="right")
    table.add_column("TARGET")
    for product in products:
        table.add_row(
            str(product.puln_product_id),
            product.name,
            "-" if product.max_quantity is None else str(product.max_quantity),
            "yes" if product.is_target else "",
        )
    runtime.console.print(table)
    missing = sorted(targets - {p.puln_product_id for p in products})
    if missing:
        runtime.console.print(
            f"Target IDs without a name: {', '.join(map(str, missing))}", markup=False
        )
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
