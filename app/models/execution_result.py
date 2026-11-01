"""Typed, non-authoritative outcomes from order orchestration."""

from dataclasses import dataclass
from enum import StrEnum


class ExecutionOutcome(StrEnum):
    READY_FOR_OPERATOR = "READY_FOR_OPERATOR"
    WAITING_FOR_OPENING = "WAITING_FOR_OPENING"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    SLOT_FULL = "SLOT_FULL"
    SESSION_UNAVAILABLE = "SESSION_UNAVAILABLE"
    LIVE_DATA_UNAVAILABLE = "LIVE_DATA_UNAVAILABLE"
    PRODUCT_UNAVAILABLE = "PRODUCT_UNAVAILABLE"


@dataclass(frozen=True)
class ExecutionResult:
    """A point-in-time assessment; never proof of a successful reservation."""

    booking_id: int
    outcome: ExecutionOutcome
    detail: str
    stock_remaining: int | None = None
    slot_remaining: int | None = None
