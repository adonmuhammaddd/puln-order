from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.clients.puln_client import LiveOk, PageCheck
from app.database.engine import create_session_factory, init_db
from app.database.tables import Booking
from app.models.booking import BookingStatus
from app.models.customer import CustomerInput
from app.models.execution_result import ExecutionOutcome
from app.models.puln import parse_live_status
from app.repositories import product_repository, snapshot_repository
from app.services.booking_service import BookingService, PreparationRequest
from app.services.customer_service import CustomerService
from app.services.order_orchestrator import OrderOrchestrator
from app.services.order_queue import OrderQueue, OrderQueueFullError
from app.services.session_service import SessionRef

SLUG = "online-order-20261031"
SESSION = SessionRef(
    slug=SLUG,
    page_url=f"https://puln.seatin.id/c/{SLUG}",
    live_url=f"https://puln.seatin.id/c/{SLUG}/live.json",
)
SAMPLE = parse_live_status((Path(__file__).parent / "fixtures" / "live_sample.json").read_bytes())


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    factory = create_session_factory(init_db("sqlite://"))
    with factory() as db:
        product_repository.seed_known_products(db)
        snapshot_repository.record_live_state(
            db, SLUG, SAMPLE, datetime(2026, 10, 30, 23, 7, tzinfo=UTC)
        )
        db.commit()
    return factory


@pytest.fixture
def customer_id(session_factory: sessionmaker[Session]) -> int:
    customer_service = CustomerService(session_factory, max_active=10)
    return customer_service.create(
        CustomerInput(
            display_name="Synthetic Customer",
            whatsapp="081200000001",
            email="customer@example.test",
        ),
        consent_confirmed=True,
    ).id


@pytest.fixture
def booking_service(session_factory: sessionmaker[Session]) -> BookingService:
    return BookingService(session_factory, session_slug=SLUG)


def prepare(
    booking_service: BookingService,
    customer_id: int,
    *,
    slot_id: int | None = 185,
) -> Booking:
    return booking_service.prepare(
        PreparationRequest(
            customer_id=customer_id,
            product_id=100,
            quantity=1,
            slot_id=slot_id,
            notes=None,
        ),
        ready=True,
    )


def make_live(*, accepting: bool = True, stock: int = 5, slot_remaining: int = 5) -> LiveOk:
    products = [p.model_copy(update={"stock_remaining": stock}) for p in SAMPLE.products]
    slots = [s.model_copy(update={"remaining": slot_remaining}) for s in SAMPLE.slots]
    status = SAMPLE.model_copy(
        update={"accepting": accepting, "products": products, "slots": slots}
    )
    return LiveOk(200, 10, status, frozenset())


def make_orchestrator(
    booking_service: BookingService,
    live: object | None = None,
    *,
    reachable: bool = True,
    queue: OrderQueue | None = None,
) -> OrderOrchestrator:
    adapter = SimpleNamespace(
        inspect=lambda session: SimpleNamespace(
            page=PageCheck(
                reachable,
                200 if reachable else 503,
                "session page found" if reachable else "HTTP 503",
            ),
            live=live or make_live(),
        )
    )
    return OrderOrchestrator(
        session=SESSION,
        backend=adapter,  # type: ignore[arg-type]
        bookings=booking_service,
        queue=queue,
    )


def test_enqueue_only_ready_bookings_and_is_idempotent(
    booking_service: BookingService, customer_id: int
) -> None:
    first = prepare(booking_service, customer_id)
    orchestrator = make_orchestrator(booking_service)

    result = orchestrator.enqueue_ready_bookings()
    repeated = orchestrator.enqueue_ready_bookings()

    assert result.enqueued_ids == (first.id,)
    assert repeated.already_queued_ids == (first.id,)
    assert len(orchestrator.queue) == 1


def test_queue_is_fifo_and_rejects_overflow() -> None:
    queue = OrderQueue(max_pending=2)
    assert queue.enqueue(10) is True
    assert queue.enqueue(10) is False
    queue.enqueue(11)
    with pytest.raises(OrderQueueFullError):
        queue.enqueue(12)
    assert queue.peek_all() == (10, 11)
    assert queue.remove(10) is True
    assert queue.peek_all() == (11,)


def test_orchestrator_rejects_mismatched_session(booking_service: BookingService) -> None:
    other = SessionRef(
        "another",
        "https://puln.seatin.id/c/another",
        "https://puln.seatin.id/c/another/live.json",
    )
    with pytest.raises(ValueError, match="same slug"):
        OrderOrchestrator(session=other, backend=object(), bookings=booking_service)  # type: ignore[arg-type]


def test_evaluate_marks_available_booking_ready_for_operator(
    booking_service: BookingService, customer_id: int
) -> None:
    booking = prepare(booking_service, customer_id)
    orchestrator = make_orchestrator(booking_service)
    orchestrator.enqueue_ready_bookings()

    result = orchestrator.evaluate_queue()

    assert result[0].booking_id == booking.id
    assert result[0].outcome is ExecutionOutcome.READY_FOR_OPERATOR
    assert result[0].stock_remaining == 5
    assert booking_service.get(booking.id).status is BookingStatus.READY


def test_evaluate_waits_when_session_not_accepting(
    booking_service: BookingService, customer_id: int
) -> None:
    prepare(booking_service, customer_id)
    orchestrator = make_orchestrator(booking_service, make_live(accepting=False))
    orchestrator.enqueue_ready_bookings()

    assert orchestrator.evaluate_queue()[0].outcome is ExecutionOutcome.WAITING_FOR_OPENING


def test_evaluate_does_not_claim_stock_when_empty(
    booking_service: BookingService, customer_id: int
) -> None:
    prepare(booking_service, customer_id)
    orchestrator = make_orchestrator(booking_service, make_live(stock=0))
    orchestrator.enqueue_ready_bookings()

    assert orchestrator.evaluate_queue()[0].outcome is ExecutionOutcome.OUT_OF_STOCK


def test_evaluate_blocks_when_slot_is_full(
    booking_service: BookingService, customer_id: int
) -> None:
    prepare(booking_service, customer_id)
    orchestrator = make_orchestrator(booking_service, make_live(slot_remaining=0))
    orchestrator.enqueue_ready_bookings()

    assert orchestrator.evaluate_queue()[0].outcome is ExecutionOutcome.SLOT_FULL


def test_evaluate_blocks_when_session_page_is_unavailable(
    booking_service: BookingService, customer_id: int
) -> None:
    prepare(booking_service, customer_id)
    orchestrator = make_orchestrator(booking_service, reachable=False)
    orchestrator.enqueue_ready_bookings()

    assert orchestrator.evaluate_queue()[0].outcome is ExecutionOutcome.SESSION_UNAVAILABLE


def test_evaluate_blocks_untrusted_live_payload(
    booking_service: BookingService, customer_id: int
) -> None:
    prepare(booking_service, customer_id)
    bad_live = SimpleNamespace(outcome="invalid")
    orchestrator = make_orchestrator(booking_service, bad_live)
    orchestrator.enqueue_ready_bookings()

    assert orchestrator.evaluate_queue()[0].outcome is ExecutionOutcome.LIVE_DATA_UNAVAILABLE


def test_stale_queue_booking_is_not_marked_ready(
    booking_service: BookingService, customer_id: int
) -> None:
    booking = prepare(booking_service, customer_id)
    orchestrator = make_orchestrator(booking_service)
    orchestrator.enqueue_ready_bookings()
    booking_service.transition(booking.id, BookingStatus.CANCELLED)

    result = orchestrator.evaluate_queue()

    assert result[0].outcome is ExecutionOutcome.LIVE_DATA_UNAVAILABLE
    assert "CANCELLED" in result[0].detail
