"""Customer queries. Full CRUD and validation arrive with customer management (Phase 3)."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.tables import Customer


def count_active(session: Session) -> int:
    return session.scalar(select(func.count()).where(Customer.is_active.is_(True))) or 0
