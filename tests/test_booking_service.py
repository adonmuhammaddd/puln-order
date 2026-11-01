import logging
from decimal import Decimal
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.database.engine import create_session_factory, init_db
from app.models.booking import BookingStatus
from app.models.catalogue import CatalogueProduct
from app.models.customer import CustomerInput
from app.models.puln import parse_live_status
from app.parsers.reservation_response_parser import (
    ReservationReview,
    parse_reservation_response,
)
from app.repositories import product_repository, session_product_repository, snapshot_repository
from app.services.booking_service import (
    AVAILABILITY_WARNING,
    BookingNotFoundError,
    BookingService,
    BookingValidationError,
    ConfirmationRequiredError,
    InvalidTransitionError,
    PreparationRequest,
)
from app.services.customer_service import CustomerService

SLUG = "online-order-20261031"
SAMPLE = parse_live_status((Path(__file__).parent / "fixtures" / "live_sample.json").read_bytes())
OBSERVED = datetime(2026, 10, 30, 23, 7, tzinfo=UTC)
S = BookingStatus


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 31, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    factory = create_session_factory(init_db("sqlite://"))
    with factory() as db:
        product_repository.seed_known_products(db)
        product_repository.set_product_name(db, 100, "Kataifi Rolls")
        product_repository.set_max_quantity(db, 100, 1)
        product_repository.set_product_name(db, 102, "Example Product")
        snapshot_repository.record_live_state(db, SLUG, SAMPLE, OBSERVED)
        db.commit()
    return factory


@pytest.fixture
def customers(session_factory: sessionmaker[Session]) -> CustomerService:
    return CustomerService(session_factory, max_active=10)


@pytest.fixture
def customer_id(customers: CustomerService) -> int:
    data = CustomerInput(
        display_name="Synthetic Customer",
        whatsapp="081200000001",
        email="customer@example.test",
    )
    return customers.create(data, consent_confirmed=True).id


@pytest.fixture
def service(session_factory: sessionmaker[Session]) -> BookingService:
    return BookingService(session_factory, session_slug=SLUG, clock=FakeClock())


def request(owner: int, **overrides: object) -> PreparationRequest:
    values: dict[str, object] = {
        "customer_id": owner,
        "product_id": 100,
        "quantity": 1,
        "slot_id": 185,
        "notes": None,
    }
    values.update(overrides)
    return PreparationRequest(**values)  # type: ignore[arg-type]


def advance(service: BookingService, booking_id: int, *statuses: BookingStatus) -> None:
    for status in statuses:
        service.transition(booking_id, status)


# Preparation checks


def test_check_always_warns_that_availability_can_change(
    service: BookingService, customer_id: int
) -> None:
    check = service.check(request(customer_id, product_id=102))

    assert AVAILABILITY_WARNING in check.warnings
    assert check.product_name == "Example Product"
    assert check.slot_label == "Sabtu, 31 Oktober 2026, 11:00\u201312:00"
    assert check.stock_remaining == 39
    assert check.data_observed_at == OBSERVED


def test_check_warns_about_zero_stock(service: BookingService, customer_id: int) -> None:
    check = service.check(request(customer_id))

    assert any("stock is 0" in warning for warning in check.warnings)


def test_check_warns_about_full_slot(service: BookingService, customer_id: int) -> None:
    check = service.check(request(customer_id, slot_id=179))

    assert any("no remaining capacity" in warning for warning in check.warnings)


def test_check_warns_when_slot_is_no_longer_listed(
    service: BookingService, customer_id: int, session_factory: sessionmaker[Session]
) -> None:
    later = SAMPLE.model_copy(update={"slots": [s for s in SAMPLE.slots if s.id != 185]})
    with session_factory() as db:
        snapshot_repository.record_live_state(db, SLUG, later, OBSERVED + timedelta(hours=1))
        db.commit()

    check = service.check(request(customer_id))

    assert any("no longer listed" in warning for warning in check.warnings)


def test_check_warns_when_product_has_no_local_limit(
    service: BookingService, customer_id: int
) -> None:
    check = service.check(request(customer_id, product_id=102))

    assert any("No local quantity limit" in warning for warning in check.warnings)


def test_check_without_live_data_warns(
    session_factory: sessionmaker[Session], customer_id: int
) -> None:
    other = BookingService(session_factory, session_slug="online-order-20261107", clock=FakeClock())

    check = other.check(request(customer_id, slot_id=None))

    assert any("No live data" in warning for warning in check.warnings)
    assert check.stock_remaining is None


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"quantity": 0}, "at least 1"),
        ({"quantity": 2}, "limit of 1"),
        ({"product_id": 555}, "products name"),
        ({"slot_id": 999}, "slot 999"),
        ({"customer_id": 404}, "customer 404"),
        ({"notes": "x" * 1001}, "notes"),
    ],
)
def test_check_rejects_invalid_requests(
    service: BookingService, customer_id: int, overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(BookingValidationError, match=message):
        service.check(request(customer_id, **overrides))


def test_inactive_customer_cannot_be_prepared(
    service: BookingService, customers: CustomerService, customer_id: int
) -> None:
    customers.deactivate(customer_id)

    with pytest.raises(BookingValidationError, match="not active"):
        service.check(request(customer_id))


# Preparation


def test_prepare_ready_records_initial_history(service: BookingService, customer_id: int) -> None:
    booking = service.prepare(request(customer_id, notes=" second choice 12:00 "), ready=True)

    assert booking.status is S.READY
    assert booking.session_slug == SLUG
    assert booking.operator_notes == "second choice 12:00"
    history = service.history(booking.id)
    assert [(h.from_status, h.to_status) for h in history] == [(None, S.READY)]


def test_prepare_as_draft(service: BookingService, customer_id: int) -> None:
    assert service.prepare(request(customer_id), ready=False).status is S.DRAFT


def test_one_open_booking_per_customer_product_and_session(
    service: BookingService, customer_id: int
) -> None:
    first = service.prepare(request(customer_id), ready=True)

    with pytest.raises(BookingValidationError, match=f"booking {first.id}"):
        service.prepare(request(customer_id, slot_id=178), ready=True)

    service.transition(first.id, S.CANCELLED)
    service.prepare(request(customer_id, slot_id=178), ready=True)


def test_other_products_can_be_prepared_for_same_customer(
    service: BookingService, customer_id: int
) -> None:
    service.prepare(request(customer_id), ready=True)

    service.prepare(request(customer_id, product_id=102), ready=True)


# Transitions


def test_valid_transition_is_recorded_with_note(service: BookingService, customer_id: int) -> None:
    booking = service.prepare(request(customer_id), ready=True)

    updated = service.transition(booking.id, S.OFFICIAL_FLOW_OPENED, note="opened on laptop")

    assert updated.status is S.OFFICIAL_FLOW_OPENED
    last = service.history(booking.id)[-1]
    assert (last.from_status, last.to_status, last.note) == (
        S.READY,
        S.OFFICIAL_FLOW_OPENED,
        "opened on laptop",
    )


def test_invalid_transition_is_refused(service: BookingService, customer_id: int) -> None:
    booking = service.prepare(request(customer_id), ready=False)

    with pytest.raises(InvalidTransitionError, match="DRAFT -> SUBMITTED_BY_USER"):
        service.transition(booking.id, S.SUBMITTED_BY_USER)

    assert service.get(booking.id).status is S.DRAFT


def test_terminal_status_cannot_change(service: BookingService, customer_id: int) -> None:
    booking = service.prepare(request(customer_id), ready=True)
    service.transition(booking.id, S.CANCELLED)

    with pytest.raises(InvalidTransitionError):
        service.transition(booking.id, S.READY)


def test_generic_transition_never_confirms(service: BookingService, customer_id: int) -> None:
    booking = service.prepare(request(customer_id), ready=True)
    advance(service, booking.id, S.OFFICIAL_FLOW_OPENED, S.SUBMITTED_BY_USER, S.AWAITING_EMAIL)

    with pytest.raises(ConfirmationRequiredError):
        service.transition(booking.id, S.CONFIRMED)


# Confirmation


def test_confirm_requires_verified_email(service: BookingService, customer_id: int) -> None:
    booking = service.prepare(request(customer_id), ready=True)
    advance(service, booking.id, S.OFFICIAL_FLOW_OPENED, S.SUBMITTED_BY_USER, S.AWAITING_EMAIL)

    with pytest.raises(ConfirmationRequiredError, match="email"):
        service.confirm(booking.id, email_verified=False, reference=None)

    assert service.get(booking.id).status is S.AWAITING_EMAIL


def test_confirm_from_awaiting_email(service: BookingService, customer_id: int) -> None:
    booking = service.prepare(request(customer_id), ready=True)
    advance(service, booking.id, S.OFFICIAL_FLOW_OPENED, S.SUBMITTED_BY_USER, S.AWAITING_EMAIL)

    confirmed = service.confirm(booking.id, email_verified=True, reference="  PULN-ABC123 ")

    assert confirmed.status is S.CONFIRMED
    assert confirmed.confirmation_ref == "PULN-ABC123"
    assert confirmed.confirmed_at is not None
    assert service.history(booking.id)[-1].to_status is S.CONFIRMED


def test_confirm_without_reference_is_allowed(service: BookingService, customer_id: int) -> None:
    booking = service.prepare(request(customer_id), ready=True)
    advance(service, booking.id, S.OFFICIAL_FLOW_OPENED, S.SUBMITTED_BY_USER, S.AWAITING_EMAIL)

    assert service.confirm(booking.id, email_verified=True, reference="").confirmation_ref is None


def test_confirm_rejects_overlong_reference(service: BookingService, customer_id: int) -> None:
    booking = service.prepare(request(customer_id), ready=True)
    advance(service, booking.id, S.OFFICIAL_FLOW_OPENED, S.SUBMITTED_BY_USER, S.AWAITING_EMAIL)

    with pytest.raises(BookingValidationError, match="reference"):
        service.confirm(booking.id, email_verified=True, reference="x" * 121)


@pytest.mark.parametrize(
    "path",
    [
        (),
        (S.OFFICIAL_FLOW_OPENED, S.SUBMITTED_BY_USER),
        (S.OFFICIAL_FLOW_OPENED, S.SUBMITTED_BY_USER, S.SUCCESS_PAGE_REVIEWED),
    ],
)
def test_confirm_only_from_awaiting_email(
    service: BookingService, customer_id: int, path: tuple[BookingStatus, ...]
) -> None:
    booking = service.prepare(request(customer_id), ready=True)
    advance(service, booking.id, *path)

    with pytest.raises(InvalidTransitionError):
        service.confirm(booking.id, email_verified=True, reference=None)


# Queries


def test_get_unknown_booking(service: BookingService) -> None:
    with pytest.raises(BookingNotFoundError, match="booking 9"):
        service.get(9)


def test_list_filters_by_session(
    service: BookingService, customer_id: int, session_factory: sessionmaker[Session]
) -> None:
    mine = service.prepare(request(customer_id), ready=True)
    other = BookingService(session_factory, session_slug="online-order-20261107", clock=FakeClock())
    theirs = other.prepare(request(customer_id, slot_id=None), ready=False)

    assert [b.id for b in service.list_bookings()] == [mine.id]
    assert [b.id for b in service.list_bookings(all_sessions=True)] == [mine.id, theirs.id]


def test_service_never_logs_notes_or_customer_data(
    service: BookingService, customer_id: int, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    booking = service.prepare(request(customer_id, notes="call 0812 first"), ready=True)
    service.transition(booking.id, S.OFFICIAL_FLOW_OPENED, note="customer said hi")

    assert "0812" not in caplog.text
    assert "said hi" not in caplog.text
    assert "Synthetic" not in caplog.text
    assert f"booking {booking.id}" in caplog.text


# Page review


RESPONSES = Path(__file__).parent / "fixtures" / "responses"


def review_of(name: str) -> ReservationReview:
    return parse_reservation_response((RESPONSES / name).read_text())


def submitted(service: BookingService, customer_id: int) -> int:
    booking = service.prepare(request(customer_id), ready=True)
    advance(service, booking.id, S.OFFICIAL_FLOW_OPENED, S.SUBMITTED_BY_USER)
    return booking.id


def test_success_review_moves_submitted_booking_forward(
    service: BookingService, customer_id: int
) -> None:
    booking_id = submitted(service, customer_id)

    booking = service.record_page_review(booking_id, review_of("success_with_reference.html"))

    assert booking.status is S.SUCCESS_PAGE_REVIEWED
    note = service.history(booking_id)[-1].note
    assert note is not None
    assert "Rp 105.000" in note
    assert "PULN-7KQ2" in note


def test_success_review_never_confirms(service: BookingService, customer_id: int) -> None:
    booking_id = submitted(service, customer_id)

    service.record_page_review(booking_id, review_of("success.html"))

    assert service.get(booking_id).status is not S.CONFIRMED
    assert service.get(booking_id).confirmed_at is None


def test_success_review_requires_submitted_status(
    service: BookingService, customer_id: int
) -> None:
    booking = service.prepare(request(customer_id), ready=True)

    with pytest.raises(InvalidTransitionError, match="READY -> SUCCESS_PAGE_REVIEWED"):
        service.record_page_review(booking.id, review_of("success.html"))


def test_failure_review_marks_failed(service: BookingService, customer_id: int) -> None:
    booking_id = submitted(service, customer_id)

    booking = service.record_page_review(booking_id, review_of("failure.html"))

    assert booking.status is S.FAILED
    assert "Reservasi gagal" in (service.history(booking_id)[-1].note or "")


@pytest.mark.parametrize("page", ["challenge.html", "title_only.html", "conflicting.html"])
def test_ambiguous_review_changes_nothing(
    service: BookingService, customer_id: int, page: str
) -> None:
    booking_id = submitted(service, customer_id)

    with pytest.raises(BookingValidationError, match="manual review"):
        service.record_page_review(booking_id, review_of(page))

    assert service.get(booking_id).status is S.SUBMITTED_BY_USER



def test_synced_session_catalogue_overrides_legacy_product_ids(
    service: BookingService,
    customer_id: int,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        session_product_repository.upsert_catalogue(
            db,
            SLUG,
            [
                CatalogueProduct(
                    105,
                    "kataifi-rolls",
                    "Kataifi Crunch (Personal)",
                    Decimal("70000.00"),
                    10,
                    None,
                )
            ],
        )
        db.commit()

    check = service.check(request(customer_id, product_id=105, quantity=2))
    assert check.product_name == "Kataifi Crunch (Personal)"
    assert check.max_quantity is None

    with pytest.raises(BookingValidationError, match="products sync"):
        service.check(request(customer_id, product_id=100))
