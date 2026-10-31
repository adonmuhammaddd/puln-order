import pytest
from pydantic import ValidationError

from app.models.customer import (
    CustomerInput,
    CustomerUpdate,
    mask_email,
    mask_phone,
    normalize_email,
    normalize_name,
    normalize_notes,
    normalize_whatsapp,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("081234567890", "+6281234567890"),
        ("0812-3456-7890", "+6281234567890"),
        ("+62 812 3456 7890", "+6281234567890"),
        ("6281234567890", "+6281234567890"),
        ("(0812) 3456.7890", "+6281234567890"),
        ("+65 9123 4567", "+6591234567"),
    ],
)
def test_whatsapp_numbers_are_normalised(raw: str, expected: str) -> None:
    assert normalize_whatsapp(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "12345", "abc", "+62", "0812345", "0215551234", "+1234567890123456", "08123abc4567"],
)
def test_invalid_whatsapp_numbers_are_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match="WhatsApp"):
        normalize_whatsapp(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Budi@Example.COM", "budi@example.com"),
        ("  siti.rahma+puln@mail.example.co.id ", "siti.rahma+puln@mail.example.co.id"),
    ],
)
def test_emails_are_normalised(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "no-at-sign",
        "a@b",
        "a b@example.com",
        "@example.com",
        "a@@example.com",
        "a@.com",
        f"{'x' * 250}@example.com",
    ],
)
def test_invalid_emails_are_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match="email"):
        normalize_email(raw)


def test_names_are_trimmed_and_whitespace_collapsed() -> None:
    assert normalize_name("  Budi   Santoso ") == "Budi Santoso"


@pytest.mark.parametrize("raw", ["", "   ", "x" * 121, "Budi\x00Santoso"])
def test_invalid_names_are_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match="name"):
        normalize_name(raw)


def test_blank_notes_become_none() -> None:
    assert normalize_notes("   ") is None
    assert normalize_notes(None) is None
    assert normalize_notes(" prefers pickup at noon ") == "prefers pickup at noon"


def test_overlong_notes_are_rejected() -> None:
    with pytest.raises(ValueError, match="notes"):
        normalize_notes("x" * 1001)


def test_customer_input_normalises_all_fields() -> None:
    data = CustomerInput(
        display_name=" Budi  Santoso ",
        whatsapp="0812-3456-7890",
        email="Budi@Example.com",
        notes="",
    )

    assert data.display_name == "Budi Santoso"
    assert data.whatsapp == "+6281234567890"
    assert data.email == "budi@example.com"
    assert data.notes is None


def test_customer_input_reports_invalid_fields() -> None:
    with pytest.raises(ValidationError) as exc_info:
        CustomerInput(display_name="", whatsapp="123", email="nope")

    assert {error["loc"][0] for error in exc_info.value.errors()} == {
        "display_name",
        "whatsapp",
        "email",
    }


def test_customer_update_only_carries_provided_fields() -> None:
    update = CustomerUpdate(email="NEW@example.com")

    assert update.model_dump(exclude_unset=True) == {"email": "new@example.com"}


@pytest.mark.parametrize(
    ("phone", "masked"),
    [("+6281234567890", "+62********890"), ("+6591234567", "+65*****567")],
)
def test_phone_numbers_are_masked(phone: str, masked: str) -> None:
    assert mask_phone(phone) == masked


@pytest.mark.parametrize(
    ("email", "masked"),
    [("budi.santoso@example.com", "b***@example.com"), ("a@example.com", "a***@example.com")],
)
def test_emails_are_masked(email: str, masked: str) -> None:
    assert mask_email(email) == masked


def test_masking_handles_empty_values() -> None:
    assert mask_phone("") == "-"
    assert mask_email("") == "-"
