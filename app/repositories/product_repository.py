"""Editable local product catalogue. The live endpoint carries IDs only, never names."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import KNOWN_PRODUCTS
from app.database.tables import Product

_MAX_NAME_LENGTH = 160


def _get(session: Session, product_id: int) -> Product | None:
    return session.scalars(
        select(Product).where(Product.puln_product_id == product_id)
    ).one_or_none()


def seed_known_products(session: Session) -> None:
    """Add observed products that are missing. Never overwrites operator edits."""
    for product_id, (name, max_quantity) in KNOWN_PRODUCTS.items():
        if _get(session, product_id) is None:
            session.add(Product(puln_product_id=product_id, name=name, max_quantity=max_quantity))
    session.flush()


def set_product_name(session: Session, product_id: int, name: str) -> Product:
    cleaned = name.strip()
    if not cleaned or len(cleaned) > _MAX_NAME_LENGTH:
        raise ValueError(f"product name must be 1-{_MAX_NAME_LENGTH} characters")
    product = _get(session, product_id)
    if product is None:
        product = Product(puln_product_id=product_id, name=cleaned)
        session.add(product)
    else:
        product.name = cleaned
    session.flush()
    return product


def sync_targets(session: Session, target_ids: frozenset[int]) -> None:
    """Mirror the configured TARGET_PRODUCT_IDS onto the catalogue."""
    for product in session.scalars(select(Product)):
        product.is_target = product.puln_product_id in target_ids
    session.flush()


def list_products(session: Session) -> Sequence[Product]:
    return session.scalars(select(Product).order_by(Product.puln_product_id)).all()


def product_names(session: Session) -> dict[int, str]:
    return {product.puln_product_id: product.name for product in list_products(session)}
