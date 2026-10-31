"""Customer queries. Business rules (limits, consent, duplicates) live in CustomerService."""

from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database.tables import Customer


def count_active(session: Session) -> int:
    return session.scalar(select(func.count()).where(Customer.is_active.is_(True))) or 0


def get(session: Session, customer_id: int) -> Customer | None:
    return session.get(Customer, customer_id)


def list_customers(session: Session, *, active_only: bool = False) -> Sequence[Customer]:
    query = select(Customer).order_by(Customer.id)
    if active_only:
        query = query.where(Customer.is_active.is_(True))
    return session.scalars(query).all()


def find_conflicts(
    session: Session, *, whatsapp: str | None, email: str | None, exclude_id: int | None = None
) -> Sequence[Customer]:
    """Other customers already using this WhatsApp number or e-mail address."""
    conditions = []
    if whatsapp:
        conditions.append(Customer.whatsapp == whatsapp)
    if email:
        conditions.append(Customer.email == email)
    if not conditions:
        return []
    query = select(Customer).where(or_(*conditions))
    if exclude_id is not None:
        query = query.where(Customer.id != exclude_id)
    return session.scalars(query).all()
