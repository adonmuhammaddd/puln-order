"""Open the official PULN page in the operator's browser.

Opening the page is all this does. The order form and any security check are completed by a
person in the browser; nothing here fills, submits or requests anything from PULN.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.exc import SQLAlchemyError

from app.models.booking import BookingStatus
from app.services.booking_service import BookingError, BookingService
from app.services.stock_monitor import ChangeEvent, EventKind

logger = logging.getLogger(__name__)

OpenUrl = Callable[[str], bool]

AUTO_OPEN_COOLDOWN = timedelta(seconds=60)
_RESTOCK_KINDS = frozenset({EventKind.PRODUCT_RESTOCKED, EventKind.PRODUCT_APPEARED})


@dataclass(frozen=True)
class OpenOutcome:
    booking_ids: tuple[int, ...]
    browser_failed: bool


def open_ready_bookings(bookings: BookingService, page_url: str, open_url: OpenUrl) -> OpenOutcome:
    """One tab per READY booking of the session, in ID order, each moved to OFFICIAL_FLOW_OPENED.

    Stops at the first tab the browser refuses, so the remaining bookings stay READY.
    """
    opened: list[int] = []
    for booking in bookings.list_bookings():
        if booking.status is not BookingStatus.READY:
            continue
        if not open_url(page_url):
            return OpenOutcome(tuple(opened), browser_failed=True)
        bookings.transition(booking.id, BookingStatus.OFFICIAL_FLOW_OPENED)
        opened.append(booking.id)
    return OpenOutcome(tuple(opened), browser_failed=False)


def triggers_auto_open(event: ChangeEvent, target_ids: frozenset[int]) -> bool:
    """The session started accepting orders, or a target product has stock again."""
    if event.kind is EventKind.SESSION_ACCEPTING:
        return True
    return (
        event.kind in _RESTOCK_KINDS and event.subject_id in target_ids and (event.current or 0) > 0
    )


class AutoOpener:
    """Opens the official page for monitor events, at most once per cooldown."""

    def __init__(
        self,
        bookings: BookingService,
        page_url: str,
        open_url: OpenUrl,
        *,
        target_ids: frozenset[int],
        clock: Callable[[], datetime],
        cooldown: timedelta = AUTO_OPEN_COOLDOWN,
    ) -> None:
        self._bookings = bookings
        self._page_url = page_url
        self._open_url = open_url
        self._target_ids = target_ids
        self._clock = clock
        self._cooldown = cooldown
        self._last_opened_at: datetime | None = None

    def handle(self, event: ChangeEvent) -> OpenOutcome | None:
        if not triggers_auto_open(event, self._target_ids):
            return None
        now = self._clock()
        if self._last_opened_at is not None and now - self._last_opened_at < self._cooldown:
            return None
        self._last_opened_at = now
        try:
            outcome = open_ready_bookings(self._bookings, self._page_url, self._open_url)
        except (BookingError, SQLAlchemyError):
            logger.exception("could not open READY bookings; opening the page once")
            outcome = OpenOutcome((), browser_failed=False)
        if not outcome.booking_ids and not outcome.browser_failed:
            outcome = OpenOutcome((), browser_failed=not self._open_url(self._page_url))
        logger.info(
            "auto-open after %s: bookings %s, browser failed %s",
            event.kind,
            list(outcome.booking_ids),
            outcome.browser_failed,
        )
        return outcome
