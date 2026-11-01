"""Session-scoped product mappings. PULN product IDs may change between sessions."""

from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.database.tables import SessionProduct
from app.models.catalogue import CatalogueProduct


def upsert_catalogue(
    session: Session, session_slug: str, products: Sequence[CatalogueProduct]
) -> None:
    session.execute(
        update(SessionProduct)
        .where(
            SessionProduct.session_slug == session_slug,
            SessionProduct.is_active.is_(True),
        )
        .values(is_active=False)
    )
    for item in products:
        record = session.scalars(
            select(SessionProduct).where(
                SessionProduct.session_slug == session_slug,
                SessionProduct.variant_key == item.variant_key,
            )
        ).one_or_none()
        by_id = session.scalars(
            select(SessionProduct).where(
                SessionProduct.session_slug == session_slug,
                SessionProduct.puln_product_id == item.puln_product_id,
            )
        ).one_or_none()
        if by_id is not None and by_id.variant_key != item.variant_key:
            raise ValueError(
                f"session product ID {item.puln_product_id} maps to conflicting variants"
            )
        if record is None:
            record = SessionProduct(
                session_slug=session_slug,
                puln_product_id=item.puln_product_id,
                variant_key=item.variant_key,
                name=item.name,
                price=str(item.price) if item.price is not None else None,
                stock_remaining=item.stock_remaining,
                max_quantity=item.max_quantity,
            )
            session.add(record)
        else:
            record.puln_product_id = item.puln_product_id
            record.name = item.name
            record.price = str(item.price) if item.price is not None else None
            record.stock_remaining = item.stock_remaining
            record.max_quantity = item.max_quantity
        record.is_active = True
    session.flush()


def get_by_id(session: Session, session_slug: str, product_id: int) -> SessionProduct | None:
    return session.scalars(
        select(SessionProduct).where(
            SessionProduct.session_slug == session_slug,
            SessionProduct.puln_product_id == product_id,
            SessionProduct.is_active.is_(True),
        )
    ).one_or_none()


def list_for_session(session: Session, session_slug: str) -> Sequence[SessionProduct]:
    return session.scalars(
        select(SessionProduct)
        .where(
            SessionProduct.session_slug == session_slug,
            SessionProduct.is_active.is_(True),
        )
        .order_by(SessionProduct.puln_product_id)
    ).all()


def list_all_for_session(session: Session, session_slug: str) -> Sequence[SessionProduct]:
    """Include inactive rows for historical booking labels."""
    return session.scalars(
        select(SessionProduct)
        .where(SessionProduct.session_slug == session_slug)
        .order_by(SessionProduct.puln_product_id)
    ).all()
