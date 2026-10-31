from pathlib import Path

import pytest

from app.parsers.reservation_response_parser import (
    MAX_HTML_BYTES,
    LineItem,
    ResponseTooLargeError,
    ReviewOutcome,
    compare_with_booking,
    parse_reservation_response,
    read_response_file,
)

RESPONSES = Path(__file__).parent / "fixtures" / "responses"


def test_recognises_success_page() -> None:
    review = parse_reservation_response((RESPONSES / "success.html").read_text())

    assert review.outcome is ReviewOutcome.SUCCESS
    assert review.title == "Reservasi berhasil · Puln"
    assert review.heading == "Reservasi berhasil"
    assert review.line_items == (LineItem(name="Kataifi Rolls", quantity=1, amount="Rp 45.000"),)
    assert review.total == "Rp 45.000"
    assert review.pickup == "Sabtu, 31 Oktober 2026, 12:00\u201313:00"
    assert review.location == "Toko Contoh, Jl. Sintetis No. 1"
    assert review.reference is None
    assert review.mentions_email_code is True


def test_never_extracts_hidden_inputs_scripts_or_customer_name() -> None:
    review = parse_reservation_response((RESPONSES / "success.html").read_text())

    text = repr(review)
    assert "FAKE-TURNSTILE-TOKEN" not in text
    assert "FAKE-SUBMISSION-KEY" not in text
    assert "Contoh Pelanggan" not in text


def test_extracts_visible_reference_and_list_items() -> None:
    review = parse_reservation_response((RESPONSES / "success_with_reference.html").read_text())

    assert review.outcome is ReviewOutcome.SUCCESS
    assert review.reference == "PULN-7KQ2"
    assert review.line_items == (
        LineItem(name="Kataifi Rolls", quantity=1, amount="Rp 45.000"),
        LineItem(name="Example Product", quantity=2, amount="Rp 60.000"),
    )
    assert review.total == "Rp 105.000"
    assert review.pickup == "Sabtu, 31 Oktober 2026, 13:00\u201314:00"
    assert review.location == "Toko Contoh"


def test_email_notice_is_not_mistaken_for_a_reference() -> None:
    review = parse_reservation_response((RESPONSES / "success.html").read_text())

    assert review.reference is None


def test_recognises_failure_page() -> None:
    review = parse_reservation_response((RESPONSES / "failure.html").read_text())

    assert review.outcome is ReviewOutcome.FAILURE
    assert review.heading == "Reservasi gagal"


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("challenge.html", "challenge"),
        ("title_only.html", "no success heading"),
        ("conflicting.html", "conflicting"),
        ("order_form.html", "no recognisable"),
    ],
)
def test_ambiguous_pages_need_manual_review(name: str, reason: str) -> None:
    review = parse_reservation_response((RESPONSES / name).read_text())

    assert review.outcome is ReviewOutcome.NEEDS_REVIEW
    assert any(reason in r for r in review.reasons)


@pytest.mark.parametrize("html", ["", "not html at all", "<html><body></body></html>"])
def test_empty_or_garbage_needs_review(html: str) -> None:
    assert parse_reservation_response(html).outcome is ReviewOutcome.NEEDS_REVIEW


def test_read_response_file_rejects_oversized_files(tmp_path: Path) -> None:
    big = tmp_path / "big.html"
    big.write_bytes(b" " * (MAX_HTML_BYTES + 1))

    with pytest.raises(ResponseTooLargeError):
        read_response_file(big)


def test_read_response_file_tolerates_bad_encoding(tmp_path: Path) -> None:
    page = tmp_path / "page.html"
    page.write_bytes(b"<title>Reservasi berhasil \xff</title><h1>Reservasi berhasil</h1>")

    assert parse_reservation_response(read_response_file(page)).outcome is ReviewOutcome.SUCCESS


def test_compare_flags_missing_product_and_quantity_mismatch() -> None:
    review = parse_reservation_response((RESPONSES / "success_with_reference.html").read_text())

    assert compare_with_booking(review, product_name="Kataifi Rolls", quantity=1) == []
    assert compare_with_booking(review, product_name="Kataifi Rolls", quantity=2) == [
        "Page shows quantity 1 for Kataifi Rolls; the booking has 2."
    ]
    assert compare_with_booking(review, product_name="Croissant", quantity=1) == [
        "Croissant does not appear in the line items on the page."
    ]


def test_compare_warns_when_no_line_items_were_found() -> None:
    review = parse_reservation_response((RESPONSES / "title_only.html").read_text())

    assert compare_with_booking(review, product_name="Kataifi Rolls", quantity=1) == [
        "No line items could be read from the page; check it manually."
    ]
