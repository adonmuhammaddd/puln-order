from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.puln_client import LiveOk, NetworkError, ServerError
from app.database.tables import Booking, Customer, PickupSlot, SlotSnapshot, StockSnapshot
from app.models.booking import BookingStatus
from app.models.puln import LiveStatus
from app.repositories import (
    booking_repository,
    customer_repository,
    monitor_check_repository,
    product_repository,
    snapshot_repository,
)

SLUG = "online-order-20261031"
T0 = datetime(2026, 10, 31, 0, 0, tzinfo=UTC)


def live(products: dict[int, int], slots: dict[int, int], **extra: Any) -> LiveStatus:
    return LiveStatus.model_validate(
        {
            "status": "OPEN",
            "accepting": True,
            "server_now": T0,
            "products": [
                {"id": pid, "stock_remaining": left, "stock_allocated": 60}
                for pid, left in products.items()
            ],
            "slots": [
                {"id": sid, "remaining": left, "label": f"Slot {sid}"}
                for sid, left in slots.items()
            ],
            **extra,
        }
    )


# Snapshots


def test_first_state_records_every_product_and_slot(db_session: Session) -> None:
    result = snapshot_repository.record_live_state(
        db_session, SLUG, live({100: 0, 102: 39}, {185: 31}), T0
    )

    assert (result.stock_rows, result.slot_rows) == (2, 1)
    assert len(db_session.scalars(select(StockSnapshot)).all()) == 2


def test_unchanged_state_records_nothing(db_session: Session) -> None:
    state = live({100: 0}, {185: 31})
    snapshot_repository.record_live_state(db_session, SLUG, state, T0)

    result = snapshot_repository.record_live_state(
        db_session, SLUG, state, T0 + timedelta(seconds=10)
    )

    assert (result.stock_rows, result.slot_rows) == (0, 0)


def test_only_changed_items_are_recorded(db_session: Session) -> None:
    snapshot_repository.record_live_state(db_session, SLUG, live({100: 0, 102: 39}, {185: 31}), T0)

    result = snapshot_repository.record_live_state(
        db_session, SLUG, live({100: 2, 102: 39}, {185: 30}), T0 + timedelta(seconds=10)
    )

    assert (result.stock_rows, result.slot_rows) == (1, 1)
    history = db_session.scalars(
        select(StockSnapshot)
        .where(StockSnapshot.puln_product_id == 100)
        .order_by(StockSnapshot.observed_at)
    ).all()
    assert [row.stock_remaining for row in history] == [0, 2]


def test_snapshots_are_scoped_per_session(db_session: Session) -> None:
    state = live({100: 0}, {185: 31})
    snapshot_repository.record_live_state(db_session, SLUG, state, T0)

    result = snapshot_repository.record_live_state(db_session, "online-order-20261107", state, T0)

    assert (result.stock_rows, result.slot_rows) == (1, 1)


def test_pickup_slots_keep_latest_label_and_capacity(db_session: Session) -> None:
    snapshot_repository.record_live_state(db_session, SLUG, live({}, {185: 31}), T0)
    snapshot_repository.record_live_state(
        db_session, SLUG, live({}, {185: 12}), T0 + timedelta(minutes=1)
    )

    slot = db_session.scalars(select(PickupSlot)).one()
    assert (slot.puln_slot_id, slot.label, slot.last_remaining) == (185, "Slot 185", 12)
    assert len(db_session.scalars(select(SlotSnapshot)).all()) == 2


def test_last_state_round_trips(db_session: Session) -> None:
    state = live({100: 0, 102: 39}, {185: 31}, queue_hint="ignored")
    snapshot_repository.record_live_state(db_session, SLUG, state, T0)

    loaded = snapshot_repository.load_last_state(db_session, SLUG)

    assert loaded is not None
    restored, observed_at = loaded
    assert restored.products == state.products
    assert restored.slots == state.slots
    assert observed_at == T0


def test_last_state_is_replaced_by_newer_state(db_session: Session) -> None:
    snapshot_repository.record_live_state(db_session, SLUG, live({100: 0}, {}), T0)
    later = T0 + timedelta(seconds=10)
    snapshot_repository.record_live_state(db_session, SLUG, live({100: 4}, {}), later)

    loaded = snapshot_repository.load_last_state(db_session, SLUG)

    assert loaded is not None
    assert loaded[0].products[0].stock_remaining == 4
    assert loaded[1] == later


def test_last_state_is_none_for_unknown_session(db_session: Session) -> None:
    assert snapshot_repository.load_last_state(db_session, SLUG) is None


# Product catalogue


def test_seed_adds_known_products_once(db_session: Session) -> None:
    product_repository.seed_known_products(db_session)
    product_repository.seed_known_products(db_session)

    products = product_repository.list_products(db_session)
    assert [(p.puln_product_id, p.name) for p in products] == [(100, "Kataifi Rolls")]


def test_seed_does_not_overwrite_operator_name(db_session: Session) -> None:
    product_repository.set_product_name(db_session, 100, "Kataifi Rolls (renamed)")

    product_repository.seed_known_products(db_session)

    assert product_repository.product_names(db_session) == {100: "Kataifi Rolls (renamed)"}


def test_set_product_name_creates_and_updates(db_session: Session) -> None:
    product_repository.set_product_name(db_session, 102, "Example Product")
    product_repository.set_product_name(db_session, 102, "  Better Name ")

    assert product_repository.product_names(db_session) == {102: "Better Name"}


@pytest.mark.parametrize("name", ["", "   ", "x" * 161])
def test_set_product_name_rejects_invalid_names(db_session: Session, name: str) -> None:
    with pytest.raises(ValueError, match="name"):
        product_repository.set_product_name(db_session, 102, name)


def test_sync_targets_follows_configuration(db_session: Session) -> None:
    product_repository.set_product_name(db_session, 100, "Kataifi Rolls")
    product_repository.set_product_name(db_session, 102, "Example Product")

    product_repository.sync_targets(db_session, frozenset({102}))

    targets = {p.puln_product_id: p.is_target for p in product_repository.list_products(db_session)}
    assert targets == {100: False, 102: True}


# Monitor checks


def test_records_checks_and_finds_last_success(db_session: Session) -> None:
    ok = LiveOk(http_status=200, duration_ms=120, status=live({}, {}), unknown_fields=frozenset())
    monitor_check_repository.record_check(db_session, SLUG, ok, T0)
    monitor_check_repository.record_check(
        db_session, SLUG, ServerError(http_status=503, duration_ms=40), T0 + timedelta(seconds=10)
    )

    last_success = monitor_check_repository.last_successful_check(db_session, SLUG)
    latest = monitor_check_repository.latest_check(db_session, SLUG)

    assert last_success is not None
    assert (last_success.started_at, last_success.http_status, last_success.duration_ms) == (
        T0,
        200,
        120,
    )
    assert latest is not None
    assert (latest.outcome, latest.http_status) == ("server_error", 503)


def test_network_errors_are_recorded_without_http_status(db_session: Session) -> None:
    error = NetworkError(duration_ms=10_000, kind="timeout", message="ReadTimeout")

    monitor_check_repository.record_check(db_session, SLUG, error, T0)

    check = monitor_check_repository.latest_check(db_session, SLUG)
    assert check is not None
    assert (check.outcome, check.http_status, check.detail) == ("network_error", None, "timeout")


# Dashboard counts


def test_counts_active_customers_and_bookings(db_session: Session) -> None:
    active = Customer(display_name="Synthetic A", whatsapp="0800", email="a@example.test")
    inactive = Customer(
        display_name="Synthetic B", whatsapp="0801", email="b@example.test", is_active=False
    )
    db_session.add_all([active, inactive])
    db_session.flush()
    for status in (BookingStatus.READY, BookingStatus.AWAITING_EMAIL, BookingStatus.CONFIRMED):
        db_session.add(
            Booking(
                customer_id=active.id,
                session_slug=SLUG,
                puln_product_id=100,
                quantity=1,
                status=status,
            )
        )
    db_session.commit()

    assert customer_repository.count_active(db_session) == 1
    assert booking_repository.count_prepared(db_session) == 2
    assert booking_repository.count_confirmed(db_session) == 1
