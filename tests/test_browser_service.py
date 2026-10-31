from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.database.engine import create_session_factory, init_db
from app.models.booking import BookingStatus
from app.models.customer import CustomerInput
from app.repositories import product_repository
from app.services.booking_service import BookingService, PreparationRequest
from app.services.browser_service import AutoOpener, open_ready_bookings, triggers_auto_open
from app.services.customer_service import CustomerService
from app.services.stock_monitor import ChangeEvent, EventKind, Severity

SLUG = "online-order-20261031"
PAGE_URL = "https://puln.seatin.id/c/online-order-20261031"
TARGETS = frozenset({100})
START = datetime(2026, 10, 30, 23, 0, tzinfo=UTC)

ACCEPTING = ChangeEvent(EventKind.SESSION_ACCEPTING, Severity.ALERT, "Session is accepting orders")


def stock(
    kind: EventKind, product_id: int, previous: int | None, current: int | None
) -> ChangeEvent:
    return ChangeEvent(kind, Severity.ALERT, "stock", product_id, previous, current)


class Browser:
    def __init__(self, *, works: bool = True) -> None:
        self.works = works
        self.opened: list[str] = []

    def __call__(self, url: str) -> bool:
        if self.works:
            self.opened.append(url)
        return self.works


class FakeClock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def bookings() -> BookingService:
    factory: sessionmaker[Session] = create_session_factory(init_db("sqlite://"))
    with factory() as db:
        product_repository.seed_known_products(db)
        db.commit()
    customers = CustomerService(factory, max_active=10)
    service = BookingService(factory, session_slug=SLUG)
    for n, ready in ((1, True), (2, False), (3, True)):
        customer = customers.create(
            CustomerInput(
                display_name=f"Synthetic {n}",
                whatsapp=f"08120000{n:04d}",
                email=f"c{n}@example.test",
            ),
            consent_confirmed=True,
        )
        service.prepare(
            PreparationRequest(
                customer_id=customer.id, product_id=100, quantity=1, slot_id=None, notes=None
            ),
            ready=ready,
        )
    return service


def statuses(service: BookingService) -> list[BookingStatus]:
    return [booking.status for booking in service.list_bookings()]


def test_opens_one_tab_per_ready_booking(bookings: BookingService) -> None:
    browser = Browser()

    outcome = open_ready_bookings(bookings, PAGE_URL, browser)

    assert outcome.booking_ids == (1, 3)
    assert outcome.browser_failed is False
    assert browser.opened == [PAGE_URL, PAGE_URL]
    assert statuses(bookings) == [
        BookingStatus.OFFICIAL_FLOW_OPENED,
        BookingStatus.DRAFT,
        BookingStatus.OFFICIAL_FLOW_OPENED,
    ]


def test_already_opened_bookings_are_not_opened_again(bookings: BookingService) -> None:
    open_ready_bookings(bookings, PAGE_URL, Browser())
    browser = Browser()

    outcome = open_ready_bookings(bookings, PAGE_URL, browser)

    assert outcome.booking_ids == ()
    assert browser.opened == []


def test_failed_browser_leaves_bookings_ready(bookings: BookingService) -> None:
    outcome = open_ready_bookings(bookings, PAGE_URL, Browser(works=False))

    assert outcome.booking_ids == ()
    assert outcome.browser_failed is True
    assert statuses(bookings) == [BookingStatus.READY, BookingStatus.DRAFT, BookingStatus.READY]


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (ACCEPTING, True),
        (stock(EventKind.PRODUCT_RESTOCKED, 100, 0, 3), True),
        (stock(EventKind.PRODUCT_APPEARED, 100, None, 5), True),
        (stock(EventKind.PRODUCT_APPEARED, 100, None, 0), False),
        (stock(EventKind.PRODUCT_RESTOCKED, 99, 0, 9), False),
        (stock(EventKind.PRODUCT_DECREASED, 100, 5, 4), False),
        (stock(EventKind.PRODUCT_SOLD_OUT, 100, 1, 0), False),
        (ChangeEvent(EventKind.SESSION_STOPPED_ACCEPTING, Severity.ALERT, "stopped"), False),
    ],
)
def test_triggers(event: ChangeEvent, expected: bool) -> None:
    assert triggers_auto_open(event, TARGETS) is expected


def make_opener(bookings: BookingService, browser: Browser, clock: FakeClock) -> AutoOpener:
    return AutoOpener(bookings, PAGE_URL, browser, target_ids=TARGETS, clock=clock)


def test_auto_open_opens_ready_bookings_when_session_opens(bookings: BookingService) -> None:
    browser = Browser()

    outcome = make_opener(bookings, browser, FakeClock()).handle(ACCEPTING)

    assert outcome is not None
    assert outcome.booking_ids == (1, 3)
    assert browser.opened == [PAGE_URL, PAGE_URL]


def test_auto_open_ignores_other_events(bookings: BookingService) -> None:
    browser = Browser()

    outcome = make_opener(bookings, browser, FakeClock()).handle(
        stock(EventKind.PRODUCT_DECREASED, 100, 5, 4)
    )

    assert outcome is None
    assert browser.opened == []


def test_auto_open_without_ready_bookings_opens_one_tab(bookings: BookingService) -> None:
    open_ready_bookings(bookings, PAGE_URL, Browser())
    browser = Browser()

    outcome = make_opener(bookings, browser, FakeClock()).handle(
        stock(EventKind.PRODUCT_RESTOCKED, 100, 0, 2)
    )

    assert outcome is not None
    assert outcome.booking_ids == ()
    assert outcome.browser_failed is False
    assert browser.opened == [PAGE_URL]


def test_auto_open_reports_failed_browser(bookings: BookingService) -> None:
    open_ready_bookings(bookings, PAGE_URL, Browser())

    outcome = make_opener(bookings, Browser(works=False), FakeClock()).handle(ACCEPTING)

    assert outcome is not None
    assert outcome.browser_failed is True


def test_auto_open_waits_for_cooldown(bookings: BookingService) -> None:
    browser, clock = Browser(), FakeClock()
    opener = make_opener(bookings, browser, clock)
    restock = stock(EventKind.PRODUCT_RESTOCKED, 100, 0, 2)

    assert opener.handle(ACCEPTING) is not None
    assert opener.handle(restock) is None
    clock.now += timedelta(seconds=59)
    assert opener.handle(restock) is None
    clock.now += timedelta(seconds=1)
    assert opener.handle(restock) is not None
    assert len(browser.opened) == 3
