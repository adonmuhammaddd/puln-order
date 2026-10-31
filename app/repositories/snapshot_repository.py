"""Stock and slot history. Snapshots are written only when a value changes."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.tables import LiveState, PickupSlot, SlotSnapshot, StockSnapshot
from app.models.puln import LiveStatus


@dataclass(frozen=True)
class RecordResult:
    stock_rows: int
    slot_rows: int


def _latest_stock(session: Session, slug: str) -> dict[int, tuple[int, int | None]]:
    latest_ids = (
        select(func.max(StockSnapshot.id))
        .where(StockSnapshot.session_slug == slug)
        .group_by(StockSnapshot.puln_product_id)
    )
    rows = session.scalars(select(StockSnapshot).where(StockSnapshot.id.in_(latest_ids)))
    return {row.puln_product_id: (row.stock_remaining, row.stock_allocated) for row in rows}


def _latest_slots(session: Session, slug: str) -> dict[int, int]:
    latest_ids = (
        select(func.max(SlotSnapshot.id))
        .where(SlotSnapshot.session_slug == slug)
        .group_by(SlotSnapshot.puln_slot_id)
    )
    rows = session.scalars(select(SlotSnapshot).where(SlotSnapshot.id.in_(latest_ids)))
    return {row.puln_slot_id: row.remaining for row in rows}


def _upsert_pickup_slots(session: Session, slug: str, status: LiveStatus) -> None:
    existing = {
        slot.puln_slot_id: slot
        for slot in session.scalars(select(PickupSlot).where(PickupSlot.session_slug == slug))
    }
    for live_slot in status.slots:
        row = existing.get(live_slot.id)
        if row is None:
            session.add(
                PickupSlot(
                    puln_slot_id=live_slot.id,
                    session_slug=slug,
                    label=live_slot.label,
                    last_remaining=live_slot.remaining,
                )
            )
        elif (row.label, row.last_remaining) != (live_slot.label, live_slot.remaining):
            row.label, row.last_remaining = live_slot.label, live_slot.remaining


def _save_last_state(
    session: Session, slug: str, status: LiveStatus, observed_at: datetime
) -> None:
    row = session.scalars(select(LiveState).where(LiveState.session_slug == slug)).one_or_none()
    if row is None:
        session.add(LiveState(session_slug=slug, payload=status.to_json(), observed_at=observed_at))
    else:
        row.payload, row.observed_at = status.to_json(), observed_at


def record_live_state(
    session: Session, slug: str, status: LiveStatus, observed_at: datetime
) -> RecordResult:
    """Store changed values and the latest valid state. The caller commits."""
    previous_stock = _latest_stock(session, slug)
    stock_rows = 0
    for product in status.products:
        current = (product.stock_remaining, product.stock_allocated)
        if previous_stock.get(product.id) != current:
            session.add(
                StockSnapshot(
                    session_slug=slug,
                    puln_product_id=product.id,
                    stock_remaining=product.stock_remaining,
                    stock_allocated=product.stock_allocated,
                    observed_at=observed_at,
                )
            )
            stock_rows += 1

    previous_slots = _latest_slots(session, slug)
    slot_rows = 0
    for slot in status.slots:
        if previous_slots.get(slot.id) != slot.remaining:
            session.add(
                SlotSnapshot(
                    session_slug=slug,
                    puln_slot_id=slot.id,
                    remaining=slot.remaining,
                    observed_at=observed_at,
                )
            )
            slot_rows += 1

    _upsert_pickup_slots(session, slug, status)
    _save_last_state(session, slug, status, observed_at)
    session.flush()
    return RecordResult(stock_rows=stock_rows, slot_rows=slot_rows)


def load_last_state(session: Session, slug: str) -> tuple[LiveStatus, datetime] | None:
    row = session.scalars(select(LiveState).where(LiveState.session_slug == slug)).one_or_none()
    if row is None:
        return None
    return LiveStatus.model_validate_json(row.payload), row.observed_at


def get_pickup_slot(session: Session, slug: str, slot_id: int) -> PickupSlot | None:
    return session.scalars(
        select(PickupSlot).where(
            PickupSlot.session_slug == slug, PickupSlot.puln_slot_id == slot_id
        )
    ).one_or_none()
