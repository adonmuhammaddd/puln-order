"""Booking workflow vocabulary and the only allowed status transitions.

A booking is a local coordination record, never proof that PULN accepted a reservation.
CONFIRMED is reachable only from AWAITING_EMAIL, after the operator verified the official
confirmation e-mail (enforced again in BookingService.confirm).
"""

from enum import StrEnum


class BookingStatus(StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    OFFICIAL_FLOW_OPENED = "OFFICIAL_FLOW_OPENED"
    SUBMITTED_BY_USER = "SUBMITTED_BY_USER"
    SUCCESS_PAGE_REVIEWED = "SUCCESS_PAGE_REVIEWED"
    AWAITING_EMAIL = "AWAITING_EMAIL"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


_S = BookingStatus

ALLOWED_TRANSITIONS: dict[BookingStatus, frozenset[BookingStatus]] = {
    _S.DRAFT: frozenset({_S.READY, _S.CANCELLED}),
    _S.READY: frozenset({_S.DRAFT, _S.OFFICIAL_FLOW_OPENED, _S.CANCELLED}),
    _S.OFFICIAL_FLOW_OPENED: frozenset({_S.READY, _S.SUBMITTED_BY_USER, _S.FAILED, _S.CANCELLED}),
    _S.SUBMITTED_BY_USER: frozenset(
        {_S.SUCCESS_PAGE_REVIEWED, _S.AWAITING_EMAIL, _S.FAILED, _S.CANCELLED}
    ),
    _S.SUCCESS_PAGE_REVIEWED: frozenset({_S.AWAITING_EMAIL, _S.FAILED, _S.CANCELLED}),
    _S.AWAITING_EMAIL: frozenset({_S.CONFIRMED, _S.FAILED, _S.CANCELLED}),
    _S.CONFIRMED: frozenset(),
    _S.FAILED: frozenset(),
    _S.CANCELLED: frozenset(),
}

TERMINAL_STATUSES = frozenset({_S.CONFIRMED, _S.FAILED, _S.CANCELLED})
OPEN_STATUSES = frozenset(BookingStatus) - TERMINAL_STATUSES


def can_transition(current: BookingStatus, target: BookingStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[current]
