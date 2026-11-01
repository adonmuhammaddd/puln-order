"""Small FIFO queue of locally prepared bookings awaiting operator review.

The database remains the source of truth. This queue is an in-memory working set and can be
reconstructed from READY bookings after restart.
"""

from collections import deque


class OrderQueueFullError(ValueError):
    """The configured pending-order limit has been reached."""


class OrderQueue:
    def __init__(self, *, max_pending: int = 10) -> None:
        if max_pending < 1:
            raise ValueError("max_pending must be at least 1")
        self._max_pending = max_pending
        self._items: deque[int] = deque()
        self._known: set[int] = set()

    @property
    def max_pending(self) -> int:
        return self._max_pending

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, booking_id: int) -> bool:
        return booking_id in self._known

    def enqueue(self, booking_id: int) -> bool:
        """Append a booking once. Return False for duplicates; reject overflow."""
        if booking_id in self._known:
            return False
        if len(self._items) >= self._max_pending:
            raise OrderQueueFullError(
                f"queue is full ({self._max_pending} pending bookings maximum)"
            )
        self._items.append(booking_id)
        self._known.add(booking_id)
        return True

    def peek_all(self) -> tuple[int, ...]:
        """Return a stable FIFO snapshot without removing work."""
        return tuple(self._items)

    def remove(self, booking_id: int) -> bool:
        """Remove a known item after an explicit operator workflow decision."""
        if booking_id not in self._known:
            return False
        self._items.remove(booking_id)
        self._known.remove(booking_id)
        return True
