from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.catalogue import CatalogueProduct
from app.repositories import session_product_repository


def test_product_id_mapping_is_scoped_to_session(db_session: Session) -> None:
    yesterday = [CatalogueProduct(100, "kataifi-rolls", "Kataifi Rolls", Decimal("70000"), 0, 1)]
    today = [CatalogueProduct(105, "kataifi-rolls", "Kataifi Crunch (Personal)", Decimal("70000"), 0, None)]
    session_product_repository.upsert_catalogue(db_session, "online-order-yesterday", yesterday)
    session_product_repository.upsert_catalogue(db_session, "online-order-today", today)

    old = session_product_repository.list_for_session(db_session, "online-order-yesterday")
    new = session_product_repository.list_for_session(db_session, "online-order-today")
    assert old[0].puln_product_id == 100
    assert old[0].max_quantity == 1
    assert new[0].puln_product_id == 105
    assert new[0].max_quantity is None


def test_sync_updates_id_for_same_variant_within_session(db_session: Session) -> None:
    first = CatalogueProduct(100, "kataifi-rolls", "Kataifi Rolls", None, None, None)
    changed = CatalogueProduct(105, "kataifi-rolls", "Kataifi Crunch (Personal)", None, None, None)
    session_product_repository.upsert_catalogue(db_session, "session", [first])
    session_product_repository.upsert_catalogue(db_session, "session", [changed])

    products = session_product_repository.list_for_session(db_session, "session")
    assert len(products) == 1
    assert products[0].puln_product_id == 105
