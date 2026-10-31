"""Booking workflow vocabulary. Transition rules arrive with booking coordination (Phase 4)."""

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
