"""Session-scoped product catalogue parsing."""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup


@dataclass(frozen=True)
class CatalogueProduct:
    puln_product_id: int
    variant_key: str
    name: str
    price: Decimal | None
    stock_remaining: int | None
    max_quantity: int | None


def _optional_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = int(value.strip())
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _price(value: str | None) -> Decimal | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = Decimal(value.strip())
    except InvalidOperation:
        return None
    return parsed if parsed >= 0 else None


def parse_catalogue(html: bytes | str) -> tuple[CatalogueProduct, ...]:
    """Parse official product cards; blank data-max-per stays unknown."""
    soup = BeautifulSoup(html, "html.parser")
    products: list[CatalogueProduct] = []
    seen: set[int] = set()
    for item in soup.select("li.product[data-product]"):
        try:
            product_id = int(str(item.get("data-product")))
        except ValueError:
            continue
        if product_id < 1 or product_id in seen:
            continue
        heading = item.select_one("h1, h2, h3, h4")
        heading_name = heading.get_text(" ", strip=True) if heading else ""
        name = str(item.get("data-name") or heading_name).strip()
        if not name:
            continue
        seen.add(product_id)
        key = (
            "kataifi-rolls"
            if name.casefold() == "kataifi crunch (personal)"
            else f"puln-product-{product_id}"
        )
        price_node = item.select_one("[data-price]")
        stock_node = item.select_one("[data-stock]")
        raw_stock = item.get("data-stock")
        if raw_stock is None and stock_node is not None:
            raw_stock = stock_node.get("data-stock")
        max_per = item.get("data-max-per")
        products.append(
            CatalogueProduct(
                puln_product_id=product_id,
                variant_key=key,
                name=name,
                price=_price(str(price_node.get("data-price")) if price_node else None),
                stock_remaining=_optional_int(str(raw_stock) if raw_stock is not None else None),
                max_quantity=_optional_int(str(max_per) if max_per is not None else None),
            )
        )
    return tuple(products)
