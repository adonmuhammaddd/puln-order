from decimal import Decimal

from app.models.catalogue import parse_catalogue


def test_parses_official_product_and_keeps_blank_limit_unknown() -> None:
    html = """
    <ul><li class="product is-soldout" data-product="105"
      data-name="Kataifi Crunch (Personal)" data-max-per="" data-stock="">
      <h3 id="p-105">Kataifi Crunch (Personal)</h3>
      <p class="price" data-price="70000.00">Rp 70.000</p>
      <input name="qty_105" min="0" max="0" value="0">
    </li></ul>
    """
    products = parse_catalogue(html)
    assert len(products) == 1
    product = products[0]
    assert product.puln_product_id == 105
    assert product.variant_key == "kataifi-rolls"
    assert product.name == "Kataifi Crunch (Personal)"
    assert product.price == Decimal("70000.00")
    assert product.max_quantity is None
    assert product.stock_remaining is None


def test_does_not_infer_limit_from_quantity_input_max() -> None:
    html = (
        '<li class="product" data-product="106" data-name="Other" '
        'data-max-per=""><input max="0"></li>'
    )
    assert parse_catalogue(html)[0].max_quantity is None


def test_ignores_invalid_or_duplicate_product_ids() -> None:
    html = """
    <li class="product" data-product="bad" data-name="Bad"></li>
    <li class="product" data-product="105" data-name="Kataifi Crunch (Personal)"></li>
    <li class="product" data-product="105" data-name="Duplicate"></li>
    """
    products = parse_catalogue(html)
    assert len(products) == 1
    assert products[0].name == "Kataifi Crunch (Personal)"
