"""Session reports and history from the local record (monitor checks, snapshots, bookings)."""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from itertools import pairwise

from sqlalchemy.orm import Session, sessionmaker

from app.models.booking import BookingStatus
from app.repositories import (
    booking_repository,
    customer_repository,
    monitor_check_repository,
    product_repository,
    report_repository,
)


@dataclass(frozen=True)
class MonitorSummary:
    total_checks: int
    outcomes: dict[str, int]
    average_ok_ms: int | None
    first_check_at: datetime | None
    last_check_at: datetime | None
    last_success_at: datetime | None


@dataclass(frozen=True)
class HistoryPoint:
    at: datetime
    remaining: int


@dataclass(frozen=True)
class TargetTimeline:
    product_id: int
    product_name: str
    points: list[HistoryPoint]
    restocks: list[datetime] = field(default_factory=list)


@dataclass(frozen=True)
class BookingRow:
    booking_id: int
    customer_name: str
    product_name: str
    quantity: int
    status: BookingStatus
    confirmation_ref: str | None
    confirmed_at: datetime | None


@dataclass(frozen=True)
class SessionReport:
    session_slug: str
    monitor: MonitorSummary
    bookings_by_status: dict[BookingStatus, int]
    bookings: list[BookingRow]
    targets: list[TargetTimeline]


def _restocks(points: list[HistoryPoint]) -> list[datetime]:
    return [
        current.at
        for previous, current in pairwise(points)
        if previous.remaining == 0 and current.remaining > 0
    ]


class ReportService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def _monitor_summary(self, db: Session, slug: str) -> MonitorSummary:
        outcomes = report_repository.outcome_counts(db, slug)
        average = report_repository.average_ok_duration(db, slug)
        first = report_repository.first_check(db, slug)
        last = monitor_check_repository.latest_check(db, slug)
        success = monitor_check_repository.last_successful_check(db, slug)
        return MonitorSummary(
            total_checks=sum(outcomes.values()),
            outcomes=outcomes,
            average_ok_ms=None if average is None else round(average),
            first_check_at=first.started_at if first else None,
            last_check_at=last.started_at if last else None,
            last_success_at=success.started_at if success else None,
        )

    def stock_history(self, slug: str, product_id: int) -> list[HistoryPoint]:
        with self._session_factory() as db:
            rows = report_repository.stock_history(db, slug, product_id)
        return [HistoryPoint(row.observed_at, row.stock_remaining) for row in rows]

    def slot_history(self, slug: str, slot_id: int) -> list[HistoryPoint]:
        with self._session_factory() as db:
            rows = report_repository.slot_history(db, slug, slot_id)
        return [HistoryPoint(row.observed_at, row.remaining) for row in rows]

    def session_report(self, slug: str, *, target_ids: frozenset[int]) -> SessionReport:
        with self._session_factory() as db:
            monitor = self._monitor_summary(db, slug)
            names = product_repository.product_names(db)
            customers = {c.id: c.display_name for c in customer_repository.list_customers(db)}
            bookings = booking_repository.list_bookings(db, session_slug=slug)
        rows = [
            BookingRow(
                booking_id=b.id,
                customer_name=customers.get(b.customer_id, f"#{b.customer_id}"),
                product_name=names.get(b.puln_product_id, f"Product #{b.puln_product_id}"),
                quantity=b.quantity,
                status=b.status,
                confirmation_ref=b.confirmation_ref,
                confirmed_at=b.confirmed_at,
            )
            for b in bookings
        ]
        targets = []
        for product_id in sorted(target_ids):
            points = self.stock_history(slug, product_id)
            targets.append(
                TargetTimeline(
                    product_id=product_id,
                    product_name=names.get(product_id, f"Product #{product_id}"),
                    points=points,
                    restocks=_restocks(points),
                )
            )
        return SessionReport(
            session_slug=slug,
            monitor=monitor,
            bookings_by_status=dict(Counter(row.status for row in rows)),
            bookings=rows,
            targets=targets,
        )
