"""Turn consecutive live states into change events.

Events come from state differences, so an unchanged state never repeats a notification.
Absence is not zero: a product or slot missing from the response is reported as missing.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from app.clients.puln_client import FetchResult, LiveOk
from app.models.puln import LiveStatus


class EventKind(StrEnum):
    SESSION_STATUS_CHANGED = "session_status_changed"
    SESSION_ACCEPTING = "session_accepting"
    SESSION_STOPPED_ACCEPTING = "session_stopped_accepting"
    PRODUCT_RESTOCKED = "product_restocked"
    PRODUCT_INCREASED = "product_increased"
    PRODUCT_DECREASED = "product_decreased"
    PRODUCT_SOLD_OUT = "product_sold_out"
    PRODUCT_APPEARED = "product_appeared"
    PRODUCT_MISSING = "product_missing"
    SLOT_OPENED = "slot_opened"
    SLOT_CHANGED = "slot_changed"
    SLOT_FULL = "slot_full"
    SLOT_APPEARED = "slot_appeared"
    SLOT_REMOVED = "slot_removed"
    ENDPOINT_UNAVAILABLE = "endpoint_unavailable"
    ENDPOINT_RECOVERED = "endpoint_recovered"
    SCHEMA_CHANGED = "schema_changed"
    DATABASE_ERROR = "database_error"


class Severity(StrEnum):
    ALERT = "alert"
    INFO = "info"


@dataclass(frozen=True)
class ChangeEvent:
    kind: EventKind
    severity: Severity
    message: str
    subject_id: int | None = None
    previous: int | None = None
    current: int | None = None


def product_label(product_id: int, names: Mapping[int, str]) -> str:
    name = names.get(product_id)
    return f"{name} (#{product_id})" if name else f"Product #{product_id}"


def _stock_kind(before: int, after: int) -> EventKind:
    if before == 0:
        return EventKind.PRODUCT_RESTOCKED
    if after == 0:
        return EventKind.PRODUCT_SOLD_OUT
    return EventKind.PRODUCT_INCREASED if after > before else EventKind.PRODUCT_DECREASED


def _session_events(prev: LiveStatus, curr: LiveStatus) -> list[ChangeEvent]:
    events = []
    if prev.status != curr.status:
        events.append(
            ChangeEvent(
                EventKind.SESSION_STATUS_CHANGED,
                Severity.INFO,
                f"Session status {prev.status} -> {curr.status}",
            )
        )
    if not prev.accepting and curr.accepting:
        events.append(
            ChangeEvent(EventKind.SESSION_ACCEPTING, Severity.ALERT, "Session is accepting orders")
        )
    elif prev.accepting and not curr.accepting:
        events.append(
            ChangeEvent(
                EventKind.SESSION_STOPPED_ACCEPTING,
                Severity.ALERT,
                "Session stopped accepting orders",
            )
        )
    return events


def _product_events(
    prev: LiveStatus, curr: LiveStatus, target_ids: frozenset[int], names: Mapping[int, str]
) -> list[ChangeEvent]:
    events = []
    before = {p.id: p.stock_remaining for p in prev.products}
    after = {p.id: p.stock_remaining for p in curr.products}
    for product_id, remaining in after.items():
        severity = Severity.ALERT if product_id in target_ids else Severity.INFO
        label = product_label(product_id, names)
        if product_id not in before:
            events.append(
                ChangeEvent(
                    EventKind.PRODUCT_APPEARED,
                    severity,
                    f"{label} appeared with {remaining} remaining",
                    product_id,
                    None,
                    remaining,
                )
            )
        elif before[product_id] != remaining:
            previous = before[product_id]
            events.append(
                ChangeEvent(
                    _stock_kind(previous, remaining),
                    severity,
                    f"{label} stock {previous} -> {remaining}",
                    product_id,
                    previous,
                    remaining,
                )
            )
    for product_id in before.keys() - after.keys():
        severity = Severity.ALERT if product_id in target_ids else Severity.INFO
        events.append(
            ChangeEvent(
                EventKind.PRODUCT_MISSING,
                severity,
                f"{product_label(product_id, names)} is missing from the live data",
                product_id,
                before[product_id],
                None,
            )
        )
    return events


def _slot_kind(before: int, after: int) -> tuple[EventKind, Severity]:
    if before == 0:
        return EventKind.SLOT_OPENED, Severity.ALERT
    if after == 0:
        return EventKind.SLOT_FULL, Severity.INFO
    return EventKind.SLOT_CHANGED, Severity.INFO


def _slot_events(prev: LiveStatus, curr: LiveStatus) -> list[ChangeEvent]:
    events = []
    before = {s.id: s for s in prev.slots}
    for slot in curr.slots:
        old = before.get(slot.id)
        if old is None:
            events.append(
                ChangeEvent(
                    EventKind.SLOT_APPEARED,
                    Severity.INFO,
                    f"Slot {slot.label} appeared with {slot.remaining} remaining",
                    slot.id,
                    None,
                    slot.remaining,
                )
            )
        elif old.remaining != slot.remaining:
            kind, severity = _slot_kind(old.remaining, slot.remaining)
            events.append(
                ChangeEvent(
                    kind,
                    severity,
                    f"Slot {slot.label}: {old.remaining} -> {slot.remaining}",
                    slot.id,
                    old.remaining,
                    slot.remaining,
                )
            )
    current_ids = {s.id for s in curr.slots}
    for slot_id, old in before.items():
        if slot_id not in current_ids:
            events.append(
                ChangeEvent(
                    EventKind.SLOT_REMOVED,
                    Severity.INFO,
                    f"Slot {old.label} is no longer listed",
                    slot_id,
                    old.remaining,
                    None,
                )
            )
    return events


def diff_states(
    prev: LiveStatus | None,
    curr: LiveStatus,
    *,
    target_ids: frozenset[int],
    product_names: Mapping[int, str],
) -> list[ChangeEvent]:
    """Events describing how ``curr`` differs from ``prev``. The first state is a baseline."""
    if prev is None:
        return []
    return [
        *_session_events(prev, curr),
        *_product_events(prev, curr, target_ids, product_names),
        *_slot_events(prev, curr),
    ]


def describe_result(result: FetchResult) -> str:
    if result.http_status is not None:
        return f"HTTP {result.http_status} ({result.outcome})"
    return result.outcome


class HealthTracker:
    """Emit one event when the endpoint becomes unavailable and one when it recovers."""

    def __init__(self) -> None:
        self._healthy: bool | None = None

    def observe(self, result: FetchResult) -> ChangeEvent | None:
        healthy = isinstance(result, LiveOk)
        previous, self._healthy = self._healthy, healthy
        if not healthy and previous is not False:
            return ChangeEvent(
                EventKind.ENDPOINT_UNAVAILABLE,
                Severity.ALERT,
                f"Live endpoint unavailable: {describe_result(result)}",
            )
        if healthy and previous is False:
            return ChangeEvent(
                EventKind.ENDPOINT_RECOVERED, Severity.INFO, "Live endpoint is available again"
            )
        return None


class DriftTracker:
    """Report fields the endpoint started sending that the model does not know."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def observe(self, fields: frozenset[str]) -> ChangeEvent | None:
        new = fields - self._seen
        if not new:
            return None
        self._seen |= new
        return ChangeEvent(
            EventKind.SCHEMA_CHANGED,
            Severity.INFO,
            f"Live data has new fields: {', '.join(sorted(new))}",
        )
