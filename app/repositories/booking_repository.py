"""Booking queries. Preparation and status transitions arrive in Phase 4."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.tables import Booking
from app.models.booking import BookingStatus

PREPARED_STATUSES = frozenset(
    {
        BookingStatus.READY,
        BookingStatus.OFFICIAL_FLOW_OPENED,
        BookingStatus.SUBMITTED_BY_USER,
        BookingStatus.SUCCESS_PAGE_REVIEWED,
        BookingStatus.AWAITING_EMAIL,
    }
)


def count_prepared(session: Session) -> int:
    return session.scalar(select(func.count()).where(Booking.status.in_(PREPARED_STATUSES))) or 0


def count_confirmed(session: Session) -> int:
    query = select(func.count()).where(Booking.status == BookingStatus.CONFIRMED)
    return session.scalar(query) or 0


def customer_has_bookings(session: Session, customer_id: int) -> bool:
    query = select(func.count()).where(Booking.customer_id == customer_id)
    return (session.scalar(query) or 0) > 0
