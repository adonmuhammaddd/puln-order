"""Coordinate read-only availability checks and operator-ready booking queues.

No reservation is submitted here. The official PULN checkout and any challenge remain in the
operator-controlled browser flow. A booking is CONFIRMED only through BookingService.confirm
after the official confirmation email has been verified.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.adapters.backend import SessionBackendAdapter
from app.clients.puln_client import LiveOk
from app.models.booking import BookingStatus
from app.models.execution_result import ExecutionOutcome, ExecutionResult
from app.services.booking_service import BookingService
from app.services.order_queue import OrderQueue, OrderQueueFullError
from app.services.session_service import SessionRef


@dataclass(frozen=True)
class QueueRefresh:
    enqueued_ids: tuple[int, ...]
    already_queued_ids: tuple[int, ...]
    rejected_ids: tuple[int, ...]


class OrderOrchestrator:
    """Build and assess a FIFO queue of READY bookings for one configured PULN session."""

    def __init__(
        self,
        *,
        session: SessionRef,
        backend: SessionBackendAdapter,
        bookings: BookingService,
        queue: OrderQueue | None = None,
    ) -> None:
        if bookings.session_slug != session.slug:
            raise ValueError("booking service and session reference must use the same slug")
        self._session = session
        self._backend = backend
        self._bookings = bookings
        self._queue = queue or OrderQueue(max_pending=10)

    @property
    def queue(self) -> OrderQueue:
        return self._queue

    def enqueue_ready_bookings(self) -> QueueRefresh:
        """Rebuild the working queue from READY records; never duplicate queued IDs."""
        enqueued: list[int] = []
        already: list[int] = []
        rejected: list[int] = []
        ready = (
            booking
            for booking in self._bookings.list_bookings()
            if booking.status is BookingStatus.READY
        )
        for booking in ready:
            if booking.id in self._queue:
                already.append(booking.id)
                continue
            try:
                self._queue.enqueue(booking.id)
            except OrderQueueFullError:
                rejected.append(booking.id)
            else:
                enqueued.append(booking.id)
        return QueueRefresh(tuple(enqueued), tuple(already), tuple(rejected))

    def evaluate_queue(self) -> Sequence[ExecutionResult]:
        """Inspect live availability once and assess queued bookings in FIFO order."""
        booking_ids = self._queue.peek_all()
        if not booking_ids:
            return ()

        inspection = self._backend.inspect(self._session)
        if not inspection.page.reachable:
            return tuple(
                ExecutionResult(
                    booking_id=booking_id,
                    outcome=ExecutionOutcome.SESSION_UNAVAILABLE,
                    detail=inspection.page.detail,
                )
                for booking_id in booking_ids
            )
        if not isinstance(inspection.live, LiveOk):
            outcome = ExecutionOutcome.LIVE_DATA_UNAVAILABLE
            detail = f"live status is not trustworthy ({type(inspection.live).__name__})"
            return tuple(ExecutionResult(booking_id, outcome, detail) for booking_id in booking_ids)

        live = inspection.live.status
        results: list[ExecutionResult] = []
        for booking_id in booking_ids:
            booking = self._bookings.get(booking_id)
            if booking.status is not BookingStatus.READY:
                # A stale queue item is reported, never executed.
                results.append(
                    ExecutionResult(
                        booking_id,
                        ExecutionOutcome.LIVE_DATA_UNAVAILABLE,
                        f"booking is now {booking.status}; refresh the queue",
                    )
                )
                continue
            if not live.accepting:
                results.append(
                    ExecutionResult(
                        booking_id,
                        ExecutionOutcome.WAITING_FOR_OPENING,
                        "the session is not accepting orders",
                    )
                )
                continue
            product = live.product(booking.puln_product_id)
            if product is None:
                results.append(
                    ExecutionResult(
                        booking_id,
                        ExecutionOutcome.PRODUCT_UNAVAILABLE,
                        "product is absent from the latest live status",
                    )
                )
                continue
            if product.stock_remaining < booking.quantity:
                results.append(
                    ExecutionResult(
                        booking_id,
                        ExecutionOutcome.OUT_OF_STOCK,
                        "latest stock is below the requested quantity",
                        stock_remaining=product.stock_remaining,
                    )
                )
                continue
            slot_remaining: int | None = None
            if booking.puln_slot_id is not None:
                slot = live.slot(booking.puln_slot_id)
                if slot is None:
                    results.append(
                        ExecutionResult(
                            booking_id,
                            ExecutionOutcome.SLOT_FULL,
                            "pickup slot is absent from the latest live status",
                            stock_remaining=product.stock_remaining,
                        )
                    )
                    continue
                slot_remaining = slot.remaining
                if slot_remaining < booking.quantity:
                    results.append(
                        ExecutionResult(
                            booking_id,
                            ExecutionOutcome.SLOT_FULL,
                            "latest pickup-slot capacity is below the requested quantity",
                            stock_remaining=product.stock_remaining,
                            slot_remaining=slot_remaining,
                        )
                    )
                    continue
            results.append(
                ExecutionResult(
                    booking_id,
                    ExecutionOutcome.READY_FOR_OPERATOR,
                    "latest read-only status indicates stock and slot capacity are available; "
                    "availability is not reserved",
                    stock_remaining=product.stock_remaining,
                    slot_remaining=slot_remaining,
                )
            )
        return tuple(results)
