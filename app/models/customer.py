"""Customer input validation and masking.

Only what reservation coordination needs: a name, a WhatsApp number and an e-mail address.
Never passwords, payment details or account credentials.
"""

import re
import unicodedata
from typing import Annotated

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict

NAME_MAX_LENGTH = 120
EMAIL_MAX_LENGTH = 255
NOTES_MAX_LENGTH = 1000

_PHONE_SEPARATORS = re.compile(r"[\s().-]")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s.]+(?:\.[^@\s.]+)+$")
_INDONESIA = "62"


def normalize_name(value: str) -> str:
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > NAME_MAX_LENGTH:
        raise ValueError(f"name must be 1-{NAME_MAX_LENGTH} characters")
    if any(unicodedata.category(char).startswith("C") for char in cleaned):
        raise ValueError("name must not contain control characters")
    return cleaned


def normalize_whatsapp(value: str) -> str:
    """Return the number in E.164 form (+<country><number>). Indonesian 08... is accepted."""
    compact = _PHONE_SEPARATORS.sub("", value)
    if compact.startswith("+"):
        digits = compact[1:]
    elif compact.startswith("08"):
        digits = _INDONESIA + compact[1:]
    elif compact.startswith(_INDONESIA):
        digits = compact
    else:
        raise ValueError("WhatsApp number must start with 08, 62 or +<country code>")
    if not digits.isdigit():
        raise ValueError("WhatsApp number may only contain digits and separators")
    if digits.startswith(_INDONESIA):
        national = digits[len(_INDONESIA) :]
        if not (national.startswith("8") and 9 <= len(national) <= 12):
            raise ValueError("WhatsApp number is not a valid Indonesian mobile number")
    elif not 8 <= len(digits) <= 15:
        raise ValueError("WhatsApp number must have 8-15 digits including the country code")
    return f"+{digits}"


def normalize_email(value: str) -> str:
    cleaned = value.strip().lower()
    if len(cleaned) > EMAIL_MAX_LENGTH or not _EMAIL.fullmatch(cleaned):
        raise ValueError("email address is not valid")
    return cleaned


def normalize_notes(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    cleaned = value.strip()
    if len(cleaned) > NOTES_MAX_LENGTH:
        raise ValueError(f"notes must be at most {NOTES_MAX_LENGTH} characters")
    return cleaned


DisplayName = Annotated[str, AfterValidator(normalize_name)]
WhatsApp = Annotated[str, AfterValidator(normalize_whatsapp)]
Email = Annotated[str, AfterValidator(normalize_email)]
Notes = Annotated[str | None, BeforeValidator(normalize_notes)]


class CustomerInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    display_name: DisplayName
    whatsapp: WhatsApp
    email: Email
    notes: Notes = None


class CustomerUpdate(BaseModel):
    """Fields to change; omitted fields stay as they are."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    display_name: DisplayName | None = None
    whatsapp: WhatsApp | None = None
    email: Email | None = None
    notes: Notes = None


def mask_phone(phone: str) -> str:
    if not phone:
        return "-"
    visible_head, visible_tail = 3, 3
    hidden = max(len(phone) - visible_head - visible_tail, 0)
    return f"{phone[:visible_head]}{'*' * hidden}{phone[-visible_tail:]}"


def mask_email(email: str) -> str:
    if not email or "@" not in email:
        return "-"
    local, domain = email.split("@", 1)
    return f"{local[:1]}***@{domain}"
