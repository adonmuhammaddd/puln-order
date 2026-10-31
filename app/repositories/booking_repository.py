"""Booking queries and the status audit trail. Business rules live in BookingService."""

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.tables import Booking, BookingStatusChange
from app.models.booking import OPEN_STATUSES, BookingStatus

PREPARED_STATUSES = OPEN_STATUSES - {BookingStatus.DRAFT}


def count_prepared(session: Session) -> int:
    return session.scalar(select(func.count()).where(Booking.status.in_(PREPARED_STATUSES))) or 0


def count_confirmed(session: Session) -> int:
    query = select(func.count()).where(Booking.status == BookingStatus.CONFIRMED)
    return session.scalar(query) or 0


def customer_has_bookings(session: Session, customer_id: int) -> bool:
    query = select(func.count()).where(Booking.customer_id == customer_id)
    return (session.scalar(query) or 0) > 0


def get(session: Session, booking_id: int) -> Booking | None:
    return session.get(Booking, booking_id)


def list_bookings(session: Session, *, session_slug: str | None) -> Sequence[Booking]:
    query = select(Booking).order_by(Booking.id)
    if session_slug is not None:
        query = query.where(Booking.session_slug == session_slug)
    return session.scalars(query).all()


def find_open(
    session: Session, *, customer_id: int, session_slug: str, product_id: int
) -> Booking | None:
    return session.scalars(
        select(Booking)
        .where(
            Booking.customer_id == customer_id,
            Booking.session_slug == session_slug,
            Booking.puln_product_id == product_id,
            Booking.status.in_(OPEN_STATUSES),
        )
        .limit(1)
    ).one_or_none()


def history(session: Session, booking_id: int) -> Sequence[BookingStatusChange]:
    return session.scalars(
        select(BookingStatusChange)
        .where(BookingStatusChange.booking_id == booking_id)
        .order_by(BookingStatusChange.changed_at, BookingStatusChange.id)
    ).all()
