"""Reservation preparation and tracking (spec sections 3.5 and 9.2).

The app prepares and tracks; the reservation itself happens only in the official PULN page,
completed by the customer or operator. Availability shown here is last-known data.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from app.clock import utc_now
from app.database.tables import Booking, BookingStatusChange, Product, SessionProduct
from app.models.booking import BookingStatus, can_transition
from app.models.customer import normalize_notes
from app.models.puln import LiveStatus
from app.parsers.reservation_response_parser import ReservationReview, ReviewOutcome
from app.repositories import (
    booking_repository,
    customer_repository,
    product_repository,
    session_product_repository,
    snapshot_repository,
)

logger = logging.getLogger(__name__)

AVAILABILITY_WARNING = (
    "Availability can change at any time; the official PULN page is the only source of truth."
)
REFERENCE_MAX_LENGTH = 120


class BookingError(ValueError):
    """A booking operation was refused; the message is safe to show the operator."""


class BookingNotFoundError(BookingError):
    pass


class BookingValidationError(BookingError):
    pass


class InvalidTransitionError(BookingError):
    pass


class ConfirmationRequiredError(BookingError):
    pass


@dataclass(frozen=True)
class PreparationRequest:
    customer_id: int
    product_id: int
    quantity: int
    slot_id: int | None
    notes: str | None


@dataclass(frozen=True)
class PreparationCheck:
    product_name: str
    max_quantity: int | None
    slot_label: str | None
    stock_remaining: int | None
    slot_remaining: int | None
    data_observed_at: datetime | None
    accepting: bool | None
    warnings: list[str] = field(default_factory=list)


def _clean_notes(notes: str | None) -> str | None:
    try:
        return normalize_notes(notes)
    except ValueError as error:
        raise BookingValidationError(str(error)) from None


class BookingService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        session_slug: str,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._session_factory = session_factory
        self._slug = session_slug
        self._clock = clock

    @property
    def session_slug(self) -> str:
        return self._slug

    # Preparation

    def _validate(self, db: Session, request: PreparationRequest) -> Product | SessionProduct:
        customer = customer_repository.get(db, request.customer_id)
        if customer is None:
            raise BookingValidationError(f"customer {request.customer_id} does not exist")
        if not customer.is_active or not customer.consent_confirmed:
            raise BookingValidationError(
                f"customer {request.customer_id} is not active or has no recorded consent"
            )
        session_catalogue = session_product_repository.list_for_session(db, self._slug)
        if session_catalogue:
            product = session_product_repository.get_by_id(db, self._slug, request.product_id)
        else:
            # Compatibility for sessions that have not yet been synced.
            product = product_repository.get_product(db, request.product_id)
        if product is None:
            raise BookingValidationError(
                f"product {request.product_id} is not in the catalogue for session {self._slug}; run products sync"
            )
        if request.quantity < 1:
            raise BookingValidationError("quantity must be at least 1")
        if product.max_quantity is not None and request.quantity > product.max_quantity:
            raise BookingValidationError(
                f"quantity {request.quantity} exceeds the local limit of {product.max_quantity} "
                f"for {product.name}"
            )
        _clean_notes(request.notes)
        return product

    def _slot_label(self, db: Session, slot_id: int | None) -> str | None:
        if slot_id is None:
            return None
        slot = snapshot_repository.get_pickup_slot(db, self._slug, slot_id)
        if slot is None:
            raise BookingValidationError(
                f"pickup slot {slot_id} is not known for {self._slug}; "
                "run status to load the current slots"
            )
        return slot.label

    @staticmethod
    def _availability(
        status: LiveStatus, request: PreparationRequest
    ) -> tuple[int | None, int | None, list[str]]:
        """Last known stock and slot capacity, with warnings. Never treated as a guarantee."""
        warnings = []
        if not status.accepting:
            warnings.append("The session was not accepting orders at the last check.")
        stock = None
        live_product = status.product(request.product_id)
        if live_product is None:
            warnings.append("The product was missing from the last live data.")
        else:
            stock = live_product.stock_remaining
            if stock == 0:
                warnings.append("Last known stock is 0.")
        capacity = None
        if request.slot_id is not None:
            live_slot = status.slot(request.slot_id)
            if live_slot is None:
                warnings.append("The pickup slot is no longer listed in the last live data.")
            else:
                capacity = live_slot.remaining
                if capacity == 0:
                    warnings.append("The pickup slot had no remaining capacity.")
        return stock, capacity, warnings

    def _check(self, db: Session, request: PreparationRequest) -> PreparationCheck:
        product = self._validate(db, request)
        slot_label = self._slot_label(db, request.slot_id)
        warnings = [AVAILABILITY_WARNING]
        if product.max_quantity is None:
            warnings.append(
                "No local quantity limit for this product; check the limit on the official page "
                "(products limit)."
            )
        cached = snapshot_repository.load_last_state(db, self._slug)
        if cached is None:
            warnings.append("No live data for this session yet; run status before relying on it.")
            return PreparationCheck(
                product_name=product.name,
                max_quantity=product.max_quantity,
                slot_label=slot_label,
                stock_remaining=None,
                slot_remaining=None,
                data_observed_at=None,
                accepting=None,
                warnings=warnings,
            )
        status, observed_at = cached
        stock, capacity, availability_warnings = self._availability(status, request)
        return PreparationCheck(
            product_name=product.name,
            max_quantity=product.max_quantity,
            slot_label=slot_label,
            stock_remaining=stock,
            slot_remaining=capacity,
            data_observed_at=observed_at,
            accepting=status.accepting,
            warnings=warnings + availability_warnings,
        )

    def check(self, request: PreparationRequest) -> PreparationCheck:
        """Validate a request and describe the last known availability, without saving."""
        with self._session_factory() as db:
            return self._check(db, request)

    def prepare(self, request: PreparationRequest, *, ready: bool) -> Booking:
        with self._session_factory() as db:
            self._check(db, request)
            existing = booking_repository.find_open(
                db,
                customer_id=request.customer_id,
                session_slug=self._slug,
                product_id=request.product_id,
            )
            if existing is not None:
                raise BookingValidationError(
                    f"customer {request.customer_id} already has open booking {existing.id} for "
                    "this product and session; cancel it first"
                )
            status = BookingStatus.READY if ready else BookingStatus.DRAFT
            booking = Booking(
                customer_id=request.customer_id,
                session_slug=self._slug,
                puln_product_id=request.product_id,
                quantity=request.quantity,
                puln_slot_id=request.slot_id,
                status=status,
                operator_notes=_clean_notes(request.notes),
            )
            db.add(booking)
            db.flush()
            self._record(db, booking, None, status, note=None)
            db.commit()
        logger.info("prepared booking %d as %s", booking.id, status)
        return booking

    # Status changes

    def _record(
        self,
        db: Session,
        booking: Booking,
        from_status: BookingStatus | None,
        to_status: BookingStatus,
        *,
        note: str | None,
    ) -> None:
        db.add(
            BookingStatusChange(
                booking_id=booking.id,
                from_status=from_status,
                to_status=to_status,
                note=_clean_notes(note),
                changed_at=self._clock(),
            )
        )

    def _require(self, db: Session, booking_id: int) -> Booking:
        booking = booking_repository.get(db, booking_id)
        if booking is None:
            raise BookingNotFoundError(f"booking {booking_id} does not exist")
        return booking

    @staticmethod
    def _check_transition(booking: Booking, target: BookingStatus) -> None:
        if not can_transition(booking.status, target):
            raise InvalidTransitionError(
                f"booking {booking.id} cannot move {booking.status} -> {target}"
            )

    def transition(
        self, booking_id: int, target: BookingStatus, *, note: str | None = None
    ) -> Booking:
        if target is BookingStatus.CONFIRMED:
            raise ConfirmationRequiredError(
                "use bookings confirm after verifying the official confirmation email"
            )
        with self._session_factory() as db:
            booking = self._require(db, booking_id)
            self._check_transition(booking, target)
            previous = booking.status
            booking.status = target
            self._record(db, booking, previous, target, note=note)
            db.commit()
        logger.info("booking %d: %s -> %s", booking_id, previous, target)
        return booking

    def confirm(
        self,
        booking_id: int,
        *,
        email_verified: bool,
        reference: str | None,
        note: str | None = None,
    ) -> Booking:
        """Mark CONFIRMED. Only for an operator who has seen the official confirmation email."""
        cleaned_reference = (reference or "").strip() or None
        if cleaned_reference is not None and len(cleaned_reference) > REFERENCE_MAX_LENGTH:
            raise BookingValidationError(
                f"reference must be at most {REFERENCE_MAX_LENGTH} characters"
            )
        with self._session_factory() as db:
            booking = self._require(db, booking_id)
            self._check_transition(booking, BookingStatus.CONFIRMED)
            if not email_verified:
                raise ConfirmationRequiredError(
                    "a booking is confirmed only after the official confirmation email is verified"
                )
            previous = booking.status
            booking.status = BookingStatus.CONFIRMED
            booking.confirmation_ref = cleaned_reference
            booking.confirmed_at = self._clock()
            self._record(db, booking, previous, BookingStatus.CONFIRMED, note=note)
            db.commit()
        logger.info("booking %d confirmed by operator", booking_id)
        return booking

    def record_page_review(self, booking_id: int, review: ReservationReview) -> Booking:
        """Record what the operator saw on a saved result page. Never confirms a booking."""
        if review.outcome is ReviewOutcome.NEEDS_REVIEW:
            raise BookingValidationError(
                "the page needs manual review: " + "; ".join(review.reasons)
            )
        if review.outcome is ReviewOutcome.SUCCESS:
            details = [
                f"total {review.total}" if review.total else None,
                f"reference {review.reference}" if review.reference else None,
                f"pickup {review.pickup}" if review.pickup else None,
            ]
            note = "Success page reviewed: " + (
                "; ".join(d for d in details if d) or "no details read"
            )
            target = BookingStatus.SUCCESS_PAGE_REVIEWED
        else:
            note = f"Failure page reviewed: {review.heading or review.title or 'no heading'}"
            target = BookingStatus.FAILED
        logger.info("booking %d: result page reviewed as %s", booking_id, review.outcome)
        return self.transition(booking_id, target, note=note)

    # Queries

    def get(self, booking_id: int) -> Booking:
        with self._session_factory() as db:
            return self._require(db, booking_id)

    def list_bookings(self, *, all_sessions: bool = False) -> Sequence[Booking]:
        with self._session_factory() as db:
            return booking_repository.list_bookings(
                db, session_slug=None if all_sessions else self._slug
            )

    def history(self, booking_id: int) -> Sequence[BookingStatusChange]:
        with self._session_factory() as db:
            self._require(db, booking_id)
            return booking_repository.history(db, booking_id)
