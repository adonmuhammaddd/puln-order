from typing import Any

import pytest

from app.clients.puln_client import LiveOk, NetworkError, ServerError
from app.models.puln import LiveStatus
from app.services.stock_monitor import (
    ChangeEvent,
    DriftTracker,
    EventKind,
    HealthTracker,
    Severity,
    diff_states,
)

TARGETS = frozenset({100})
NAMES = {100: "Kataifi Rolls"}


def state(
    *,
    accepting: bool = True,
    status: str = "OPEN",
    products: dict[int, int] | None = None,
    slots: dict[int, int] | None = None,
) -> LiveStatus:
    product_rows: list[dict[str, Any]] = [
        {"id": pid, "stock_remaining": remaining, "stock_allocated": 60}
        for pid, remaining in (products if products is not None else {100: 0, 102: 39}).items()
    ]
    slot_rows: list[dict[str, Any]] = [
        {"id": sid, "remaining": remaining, "label": f"Slot {sid}"}
        for sid, remaining in (slots if slots is not None else {185: 31}).items()
    ]
    return LiveStatus.model_validate(
        {"status": status, "accepting": accepting, "products": product_rows, "slots": slot_rows}
    )


def diff(prev: LiveStatus | None, curr: LiveStatus) -> list[ChangeEvent]:
    return diff_states(prev, curr, target_ids=TARGETS, product_names=NAMES)


def kinds(events: list[ChangeEvent]) -> list[EventKind]:
    return [event.kind for event in events]


def test_first_observation_is_a_baseline_without_events() -> None:
    assert diff(None, state()) == []


def test_unchanged_state_produces_no_events() -> None:
    assert diff(state(), state()) == []


def test_session_starting_to_accept_is_an_alert() -> None:
    events = diff(state(accepting=False, status="SCHEDULED"), state())

    assert kinds(events) == [EventKind.SESSION_STATUS_CHANGED, EventKind.SESSION_ACCEPTING]
    assert events[1].severity is Severity.ALERT


def test_session_stopping_is_an_alert() -> None:
    events = diff(state(), state(accepting=False))

    assert kinds(events) == [EventKind.SESSION_STOPPED_ACCEPTING]
    assert events[0].severity is Severity.ALERT


def test_target_zero_to_positive_is_a_restock_alert() -> None:
    events = diff(state(products={100: 0}), state(products={100: 3}))

    assert len(events) == 1
    event = events[0]
    assert event.kind is EventKind.PRODUCT_RESTOCKED
    assert event.severity is Severity.ALERT
    assert (event.subject_id, event.previous, event.current) == (100, 0, 3)
    assert "Kataifi Rolls" in event.message


@pytest.mark.parametrize(
    ("before", "after", "kind"),
    [
        (5, 3, EventKind.PRODUCT_DECREASED),
        (3, 0, EventKind.PRODUCT_SOLD_OUT),
        (2, 5, EventKind.PRODUCT_INCREASED),
    ],
)
def test_target_stock_changes_are_alerts(before: int, after: int, kind: EventKind) -> None:
    events = diff(state(products={100: before}), state(products={100: after}))

    assert kinds(events) == [kind]
    assert events[0].severity is Severity.ALERT


def test_non_target_changes_are_informational() -> None:
    events = diff(state(products={102: 0}), state(products={102: 4}))

    assert kinds(events) == [EventKind.PRODUCT_RESTOCKED]
    assert events[0].severity is Severity.INFO
    assert "Product #102" in events[0].message


def test_allocation_only_change_is_ignored() -> None:
    before = state(products={100: 0})
    after = LiveStatus.model_validate(
        {
            "status": "OPEN",
            "accepting": True,
            "products": [{"id": 100, "stock_remaining": 0, "stock_allocated": 80}],
            "slots": [{"id": 185, "remaining": 31, "label": "Slot 185"}],
        }
    )

    assert diff(before, after) == []


def test_missing_target_product_is_an_alert_not_zero_stock() -> None:
    events = diff(state(products={100: 2, 102: 39}), state(products={102: 39}))

    assert kinds(events) == [EventKind.PRODUCT_MISSING]
    assert events[0].severity is Severity.ALERT
    assert events[0].current is None


def test_new_product_is_reported() -> None:
    events = diff(state(products={102: 39}), state(products={102: 39, 103: 5}))

    assert kinds(events) == [EventKind.PRODUCT_APPEARED]
    assert events[0].current == 5


@pytest.mark.parametrize(
    ("before", "after", "kind", "severity"),
    [
        (31, 29, EventKind.SLOT_CHANGED, Severity.INFO),
        (2, 0, EventKind.SLOT_FULL, Severity.INFO),
        (0, 4, EventKind.SLOT_OPENED, Severity.ALERT),
    ],
)
def test_slot_capacity_changes(
    before: int, after: int, kind: EventKind, severity: Severity
) -> None:
    events = diff(state(slots={185: before}), state(slots={185: after}))

    assert kinds(events) == [kind]
    assert events[0].severity is severity


def test_slot_disappearing_is_removed_not_full() -> None:
    events = diff(state(slots={179: 0, 185: 31}), state(slots={185: 31}))

    assert kinds(events) == [EventKind.SLOT_REMOVED]
    assert events[0].subject_id == 179


def test_new_slot_is_reported() -> None:
    events = diff(state(slots={185: 31}), state(slots={185: 31, 190: 10}))

    assert kinds(events) == [EventKind.SLOT_APPEARED]


def ok() -> LiveOk:
    return LiveOk(http_status=200, duration_ms=1, status=state(), unknown_fields=frozenset())


def failure() -> ServerError:
    return ServerError(http_status=503, duration_ms=1)


def test_health_tracker_reports_outage_once() -> None:
    tracker = HealthTracker()

    assert tracker.observe(ok()) is None
    outage = tracker.observe(failure())
    assert tracker.observe(failure()) is None
    assert tracker.observe(NetworkError(duration_ms=1, kind="timeout", message="x")) is None

    assert outage is not None
    assert outage.kind is EventKind.ENDPOINT_UNAVAILABLE
    assert outage.severity is Severity.ALERT
    assert "503" in outage.message


def test_health_tracker_reports_recovery_once() -> None:
    tracker = HealthTracker()
    tracker.observe(failure())

    recovery = tracker.observe(ok())

    assert recovery is not None
    assert recovery.kind is EventKind.ENDPOINT_RECOVERED
    assert tracker.observe(ok()) is None


def test_health_tracker_reports_failure_on_first_check() -> None:
    event = HealthTracker().observe(failure())

    assert event is not None
    assert event.kind is EventKind.ENDPOINT_UNAVAILABLE


def test_drift_tracker_reports_each_new_field_once() -> None:
    tracker = DriftTracker()

    first = tracker.observe(frozenset({"queue"}))
    repeat = tracker.observe(frozenset({"queue"}))
    second = tracker.observe(frozenset({"queue", "slots[].capacity"}))

    assert first is not None
    assert first.kind is EventKind.SCHEMA_CHANGED
    assert "queue" in first.message
    assert repeat is None
    assert second is not None
    assert "slots[].capacity" in second.message
    assert "queue" not in second.message
    assert tracker.observe(frozenset()) is None
