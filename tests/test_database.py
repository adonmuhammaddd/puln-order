import stat
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError, StatementError

from app.database.engine import create_db_engine, create_session_factory, init_db, sqlite_file_path
from app.database.tables import Booking, Customer, PickupSlot, Product, StockSnapshot
from app.models.booking import BookingStatus

EXPECTED_TABLES = {
    "customers",
    "products",
    "pickup_slots",
    "stock_snapshots",
    "slot_snapshots",
    "bookings",
    "monitor_checks",
    "live_states",
}


def test_sqlite_file_path_resolves_relative_url(tmp_path: Path) -> None:
    assert sqlite_file_path("sqlite:///./data/app.db") == Path("data/app.db")
    assert sqlite_file_path(f"sqlite:///{tmp_path}/app.db") == tmp_path / "app.db"


def test_sqlite_file_path_is_none_for_memory_database() -> None:
    assert sqlite_file_path("sqlite://") is None
    assert sqlite_file_path("sqlite:///:memory:") is None


def test_rejects_non_sqlite_database_url() -> None:
    with pytest.raises(ValueError, match="SQLite"):
        create_db_engine("postgresql://localhost/puln")


def test_init_db_creates_database_file_and_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "puln.db"

    engine = init_db(f"sqlite:///{db_path}")

    assert db_path.exists()
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'puln.db'}"

    init_db(url).dispose()
    engine = init_db(url)

    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES


def test_init_db_restricts_file_permissions_to_owner(tmp_path: Path) -> None:
    db_path = tmp_path / "puln.db"

    init_db(f"sqlite:///{db_path}")

    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600


def test_foreign_keys_are_enforced() -> None:
    engine = init_db("sqlite://")
    session_factory = create_session_factory(engine)

    with session_factory() as session:
        session.add(
            Booking(
                customer_id=999,
                session_slug="online-order-20261031",
                puln_product_id=100,
                quantity=1,
                status=BookingStatus.DRAFT,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_product_id_is_unique() -> None:
    session_factory = create_session_factory(init_db("sqlite://"))

    with session_factory() as session:
        session.add_all(
            [Product(puln_product_id=100, name="A"), Product(puln_product_id=100, name="B")]
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_pickup_slot_is_unique_per_session_only() -> None:
    session_factory = create_session_factory(init_db("sqlite://"))

    with session_factory() as session:
        session.add_all(
            [
                PickupSlot(puln_slot_id=185, session_slug="online-order-20261031", label="a"),
                PickupSlot(puln_slot_id=185, session_slug="online-order-20261107", label="b"),
            ]
        )
        session.commit()
        session.add(PickupSlot(puln_slot_id=185, session_slug="online-order-20261031", label="c"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_datetimes_round_trip_as_aware_utc() -> None:
    session_factory = create_session_factory(init_db("sqlite://"))
    jakarta_time = datetime(2026, 10, 31, 6, 0, tzinfo=timezone(timedelta(hours=7)))

    with session_factory() as session:
        session.add(
            StockSnapshot(
                session_slug="online-order-20261031",
                puln_product_id=100,
                stock_remaining=0,
                stock_allocated=60,
                observed_at=jakarta_time,
            )
        )
        session.commit()

    with session_factory() as session:
        stored = session.scalars(select(StockSnapshot)).one()

    assert stored.observed_at == datetime(2026, 10, 30, 23, 0, tzinfo=UTC)
    assert stored.observed_at.tzinfo == UTC


def test_naive_datetimes_are_rejected() -> None:
    session_factory = create_session_factory(init_db("sqlite://"))

    with session_factory() as session:
        session.add(
            StockSnapshot(
                session_slug="online-order-20261031",
                puln_product_id=100,
                stock_remaining=0,
                observed_at=datetime(2026, 10, 31, 6, 0),
            )
        )
        with pytest.raises(StatementError, match="timezone-aware"):
            session.commit()


def test_timestamps_are_filled_automatically() -> None:
    session_factory = create_session_factory(init_db("sqlite://"))

    with session_factory() as session:
        customer = Customer(display_name="Synthetic Customer", whatsapp="0800", email="x@test")
        session.add(customer)
        session.commit()

    assert customer.created_at.tzinfo == UTC
    assert customer.updated_at.tzinfo == UTC
    assert customer.consent_confirmed is False
    assert customer.is_active is True


def test_booking_status_round_trips_as_enum() -> None:
    session_factory = create_session_factory(init_db("sqlite://"))

    with session_factory() as session:
        customer = Customer(display_name="Synthetic Customer", whatsapp="0800", email="x@test")
        session.add(customer)
        session.flush()
        session.add(
            Booking(
                customer_id=customer.id,
                session_slug="online-order-20261031",
                puln_product_id=100,
                quantity=1,
                status=BookingStatus.READY,
            )
        )
        session.commit()

    with session_factory() as session:
        booking = session.scalars(select(Booking)).one()

    assert booking.status is BookingStatus.READY
    assert booking.confirmed_at is None
