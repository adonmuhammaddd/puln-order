"""Terminal dashboard (spec section 11). Pure rendering: state in, Rich renderable out.

Shows no customer contact details and no request secrets; only counts and public stock data.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from app.clients.puln_client import LiveOk
from app.constants import APP_NAME
from app.services.monitor_service import MonitorState
from app.services.stock_monitor import Severity, describe_result
from app.ui.timefmt import format_local

EVENTS_SHOWN = 8


class Freshness(StrEnum):
    LIVE = "LIVE"
    STALE = "STALE"
    LAST_KNOWN = "LAST KNOWN"
    CACHED = "CACHED"
    NO_DATA = "NO DATA"


@dataclass(frozen=True)
class DashboardContext:
    timezone: str
    target_ids: frozenset[int]
    stale_after_seconds: float
    max_customers: int
    active_customers: int
    prepared_bookings: int
    confirmed_bookings: int


def freshness(state: MonitorState, ctx: DashboardContext, now: datetime) -> Freshness:
    if state.last_valid is None or state.last_valid_at is None:
        return Freshness.NO_DATA
    if state.last_result is not None and not isinstance(state.last_result, LiveOk):
        return Freshness.LAST_KNOWN
    if state.last_valid_is_cached:
        return Freshness.CACHED
    if (now - state.last_valid_at).total_seconds() > ctx.stale_after_seconds:
        return Freshness.STALE
    return Freshness.LIVE


_FRESHNESS_STYLE = {
    Freshness.LIVE: "bold green",
    Freshness.STALE: "bold yellow",
    Freshness.LAST_KNOWN: "bold yellow",
    Freshness.CACHED: "bold yellow",
    Freshness.NO_DATA: "bold red",
}


def _freshness_line(state: MonitorState, ctx: DashboardContext, now: datetime) -> Text:
    kind = freshness(state, ctx, now)
    line = Text(f"Data: {kind.value}", style=_FRESHNESS_STYLE[kind])
    if state.last_valid_at is not None and kind is not Freshness.LIVE:
        age = int((now - state.last_valid_at).total_seconds())
        as_of = format_local(state.last_valid_at, ctx.timezone)
        line.append(f"  as of {as_of} ({age}s ago)", style="yellow")
    if kind is Freshness.CACHED:
        line.append("  from a previous run, not yet refreshed", style="yellow")
    if kind is Freshness.LAST_KNOWN and state.last_result is not None:
        line.append(f"  current data unavailable: {describe_result(state.last_result)}", "red")
    if kind is Freshness.STALE:
        line.append("  availability may have changed", style="yellow")
    return line


def _header(state: MonitorState, ctx: DashboardContext, now: datetime) -> Group:
    lines = [
        Text(APP_NAME, style="bold"),
        Text(f"Session: {state.session.slug}"),
        Text(f"Local time: {format_local(now, ctx.timezone)} ({ctx.timezone})"),
    ]
    status = state.last_valid
    if status is not None:
        accepting = Text("YES", style="bold green") if status.accepting else Text("NO", "bold red")
        lines += [
            Text(f"Session status: {status.status}"),
            Text("Accepting orders: ").append_text(accepting),
            Text(f"Opens: {format_local(status.opens_at, ctx.timezone)}"),
            Text(f"Closes: {format_local(status.closes_at, ctx.timezone)}"),
            Text(f"Server time: {format_local(status.server_now, ctx.timezone)}"),
        ]
    lines.append(_freshness_line(state, ctx, now))
    return Group(*lines)


def _products(state: MonitorState, ctx: DashboardContext) -> Table:
    table = Table(title="PRODUCTS", title_justify="left", expand=False)
    table.add_column("", width=1)
    table.add_column("ID", justify="right")
    table.add_column("PRODUCT")
    table.add_column("REMAINING", justify="right")
    table.add_column("ALLOCATED", justify="right")
    products = state.last_valid.products if state.last_valid else []
    for product in sorted(products, key=lambda p: (p.id not in ctx.target_ids, p.id)):
        is_target = product.id in ctx.target_ids
        available = product.stock_remaining > 0
        style = ("bold " if is_target else "") + ("green" if available else "dim")
        table.add_row(
            "*" if is_target else "",
            str(product.id),
            state.product_names.get(product.id, f"Product #{product.id}"),
            str(product.stock_remaining),
            "-" if product.stock_allocated is None else str(product.stock_allocated),
            style=style,
        )
    return table


def _slots(state: MonitorState) -> Table:
    table = Table(title="PICKUP SLOTS", title_justify="left", expand=False)
    table.add_column("ID", justify="right")
    table.add_column("TIME LABEL")
    table.add_column("REMAINING", justify="right")
    for slot in state.last_valid.slots if state.last_valid else []:
        table.add_row(
            str(slot.id),
            slot.label,
            str(slot.remaining),
            style="green" if slot.remaining > 0 else "dim",
        )
    return table


def _customers(ctx: DashboardContext) -> Group:
    return Group(
        Text("CUSTOMERS", style="bold"),
        Text(f"Active profiles: {ctx.active_customers} / {ctx.max_customers}"),
        Text(f"Prepared bookings: {ctx.prepared_bookings}"),
        Text(f"Confirmed bookings: {ctx.confirmed_bookings}"),
    )


def _monitor(state: MonitorState, ctx: DashboardContext) -> Group:
    result = state.last_result
    if result is None:
        last_response = "-"
    elif isinstance(result, LiveOk):
        last_response = f"HTTP {result.http_status} in {result.duration_ms} ms"
    elif result.http_status is not None:
        last_response = f"{describe_result(result)} in {result.duration_ms} ms"
    else:
        last_response = f"{describe_result(result)} after {result.duration_ms} ms"
    lines = [
        Text("MONITOR", style="bold"),
        Text(f"Last successful check: {format_local(state.last_valid_at, ctx.timezone)}"),
        Text(f"Last response: {last_response}"),
    ]
    if state.consecutive_failures:
        lines.append(Text(f"Consecutive failures: {state.consecutive_failures}", style="yellow"))
    if state.stop_reason is not None:
        lines.append(Text(f"Monitoring stopped: {state.stop_reason}", style="bold red"))
    else:
        lines.append(Text(f"Next check: {format_local(state.next_check_at, ctx.timezone)}"))
    return Group(*lines)


def _events(state: MonitorState, ctx: DashboardContext) -> Group:
    lines = [Text("RECENT CHANGES", style="bold")]
    recent = list(state.events)[-EVENTS_SHOWN:]
    if not recent:
        lines.append(Text("No changes observed yet", style="dim"))
    for timed in reversed(recent):
        style = "bold red" if timed.event.severity is Severity.ALERT else ""
        stamp = format_local(timed.at, ctx.timezone, "%H:%M:%S")
        lines.append(Text(f"{stamp}  {timed.event.message}", style=style))
    return Group(*lines)


def render_dashboard(state: MonitorState, ctx: DashboardContext, now: datetime) -> RenderableType:
    spacer = Text("")
    return Group(
        _header(state, ctx, now),
        spacer,
        _products(state, ctx),
        spacer,
        _slots(state),
        spacer,
        _customers(ctx),
        spacer,
        _monitor(state, ctx),
        spacer,
        _events(state, ctx),
    )
