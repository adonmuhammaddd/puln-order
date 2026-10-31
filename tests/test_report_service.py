from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.clients.puln_client import LiveOk, NetworkError, RateLimited, ServerError
from app.database.engine import create_session_factory, init_db
from app.models.booking import BookingStatus
from app.models.customer import CustomerInput
from app.models.puln import LiveStatus
from app.repositories import monitor_check_repository, product_repository, snapshot_repository
from app.services.booking_service import BookingService, PreparationRequest
from app.services.customer_service import CustomerService
from app.services.report_service import ReportService

SLUG = "online-order-20261031"
T0 = datetime(2026, 10, 30, 23, 0, tzinfo=UTC)


def live(kataifi: int, slot_185: int) -> LiveStatus:
    payload: dict[str, Any] = {
        "status": "OPEN",
        "accepting": True,
        "products": [
            {"id": 100, "stock_remaining": kataifi, "stock_allocated": 60},
            {"id": 102, "stock_remaining": 39, "stock_allocated": 70},
        ],
        "slots": [{"id": 185, "remaining": slot_185, "label": "Slot 11:00"}],
    }
    return LiveStatus.model_validate(payload)


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    factory = create_session_factory(init_db("sqlite://"))
    with factory() as db:
        product_repository.seed_known_products(db)
        for minutes, (kataifi, slot) in enumerate([(0, 31), (0, 30), (3, 30), (1, 28), (0, 28)]):
            at = T0 + timedelta(minutes=minutes)
            snapshot_repository.record_live_state(db, SLUG, live(kataifi, slot), at)
            ok = LiveOk(
                http_status=200,
                duration_ms=100 + minutes * 10,
                status=live(kataifi, slot),
                unknown_fields=frozenset(),
            )
            monitor_check_repository.record_check(db, SLUG, ok, at)
        monitor_check_repository.record_check(
            db, SLUG, ServerError(http_status=503, duration_ms=50), T0 + timedelta(minutes=6)
        )
        monitor_check_repository.record_check(
            db,
            SLUG,
            RateLimited(http_status=429, duration_ms=5, retry_after_seconds=60),
            T0 + timedelta(minutes=7),
        )
        monitor_check_repository.record_check(
            db,
            SLUG,
            NetworkError(duration_ms=10_000, kind="timeout", message="ReadTimeout"),
            T0 + timedelta(minutes=8),
        )
        db.commit()
    customers = CustomerService(factory, max_active=10)
    bookings = BookingService(factory, session_slug=SLUG)
    for n, final in ((1, BookingStatus.CANCELLED), (2, None)):
        customer = customers.create(
            CustomerInput(
                display_name=f"Synthetic {n}",
                whatsapp=f"08120000{n:04d}",
                email=f"c{n}@example.test",
            ),
            consent_confirmed=True,
        )
        booking = bookings.prepare(
            PreparationRequest(
                customer_id=customer.id, product_id=100, quantity=1, slot_id=185, notes=None
            ),
            ready=True,
        )
        if final is not None:
            bookings.transition(booking.id, final)
    return factory


def test_monitor_summary_counts_outcomes(session_factory: sessionmaker[Session]) -> None:
    report = ReportService(session_factory).session_report(SLUG, target_ids=frozenset({100}))

    summary = report.monitor
    assert summary.total_checks == 8
    assert summary.outcomes == {
        "ok": 5,
        "server_error": 1,
        "rate_limited": 1,
        "network_error": 1,
    }
    assert summary.average_ok_ms == 120
    assert summary.first_check_at == T0
    assert summary.last_check_at == T0 + timedelta(minutes=8)
    assert summary.last_success_at == T0 + timedelta(minutes=4)


def test_bookings_are_counted_by_status(session_factory: sessionmaker[Session]) -> None:
    report = ReportService(session_factory).session_report(SLUG, target_ids=frozenset({100}))

    assert report.bookings_by_status == {BookingStatus.READY: 1, BookingStatus.CANCELLED: 1}
    assert [row.customer_name for row in report.bookings] == ["Synthetic 1", "Synthetic 2"]
    assert report.bookings[0].product_name == "Kataifi Rolls"


def test_target_stock_timeline_shows_restocks(session_factory: sessionmaker[Session]) -> None:
    report = ReportService(session_factory).session_report(SLUG, target_ids=frozenset({100}))

    (timeline,) = report.targets
    assert timeline.product_id == 100
    assert timeline.product_name == "Kataifi Rolls"
    assert [(p.at, p.remaining) for p in timeline.points] == [
        (T0, 0),
        (T0 + timedelta(minutes=2), 3),
        (T0 + timedelta(minutes=3), 1),
        (T0 + timedelta(minutes=4), 0),
    ]
    assert timeline.restocks == [T0 + timedelta(minutes=2)]


def test_stock_history_for_any_product(session_factory: sessionmaker[Session]) -> None:
    points = ReportService(session_factory).stock_history(SLUG, 102)

    assert [(p.at, p.remaining) for p in points] == [(T0, 39)]


def test_slot_history(session_factory: sessionmaker[Session]) -> None:
    points = ReportService(session_factory).slot_history(SLUG, 185)

    assert [p.remaining for p in points] == [31, 30, 28]


def test_empty_session_report(session_factory: sessionmaker[Session]) -> None:
    report = ReportService(session_factory).session_report(
        "online-order-20261107", target_ids=frozenset({100})
    )

    assert report.monitor.total_checks == 0
    assert report.monitor.average_ok_ms is None
    assert report.bookings == []
    assert report.targets[0].points == []
