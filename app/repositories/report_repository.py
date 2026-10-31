"""Read-only queries for reports and history."""

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.tables import MonitorCheck, SlotSnapshot, StockSnapshot


def outcome_counts(session: Session, slug: str) -> dict[str, int]:
    rows = session.execute(
        select(MonitorCheck.outcome, func.count())
        .where(MonitorCheck.session_slug == slug)
        .group_by(MonitorCheck.outcome)
    )
    return {outcome: count for outcome, count in rows}


def average_ok_duration(session: Session, slug: str) -> float | None:
    return session.scalar(
        select(func.avg(MonitorCheck.duration_ms)).where(
            MonitorCheck.session_slug == slug, MonitorCheck.outcome == "ok"
        )
    )


def first_check(session: Session, slug: str) -> MonitorCheck | None:
    return session.scalars(
        select(MonitorCheck)
        .where(MonitorCheck.session_slug == slug)
        .order_by(MonitorCheck.started_at, MonitorCheck.id)
        .limit(1)
    ).one_or_none()


def stock_history(session: Session, slug: str, product_id: int) -> Sequence[StockSnapshot]:
    return session.scalars(
        select(StockSnapshot)
        .where(StockSnapshot.session_slug == slug, StockSnapshot.puln_product_id == product_id)
        .order_by(StockSnapshot.observed_at, StockSnapshot.id)
    ).all()


def slot_history(session: Session, slug: str, slot_id: int) -> Sequence[SlotSnapshot]:
    return session.scalars(
        select(SlotSnapshot)
        .where(SlotSnapshot.session_slug == slug, SlotSnapshot.puln_slot_id == slot_id)
        .order_by(SlotSnapshot.observed_at, SlotSnapshot.id)
    ).all()
