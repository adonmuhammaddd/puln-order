"""Read a reservation result page the operator saved from the official PULN flow.

Used only to help a person review what happened. HTTP 200 proves nothing; the outcome is
SUCCESS only when the page carries a recognisable success heading, and anything unclear is
NEEDS_REVIEW. The parser ignores scripts, styles and form fields (which may hold tokens), and
does not extract the customer's name or contact details.

The real page structure has been observed only once; extraction of line items, total, pickup,
location and reference is best effort and should be checked against a saved real page.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

MAX_HTML_BYTES = 2_000_000

_SUCCESS = re.compile(r"\b(reservasi|pesanan)\s+berhasil\b", re.IGNORECASE)
_FAILURE = re.compile(
    r"\b(gagal|tidak berhasil|habis|sold out|tidak tersedia|ditolak|kesalahan|error)\b",
    re.IGNORECASE,
)
_CHALLENGE = ("just a moment", "verifying you are human", "attention required", "cf-chl")
_AMOUNT = re.compile(r"Rp\s?[\d.,]+")
_QUANTITY = re.compile(r"(?:\b(\d+)\s*[x\u00d7])|(?:[x\u00d7]\s*(\d+)\b)")
_PICKUP_LABEL = re.compile(
    r"^\s*(?:waktu pengambilan|jadwal pengambilan|pengambilan|pickup)\b\s*:?", re.IGNORECASE
)
_LOCATION_LABEL = re.compile(r"^\s*(?:lokasi|alamat|location)\b\s*:?", re.IGNORECASE)
_REFERENCE_LABEL = re.compile(
    r"^\s*(?:kode reservasi|kode booking|nomor reservasi|reservation code|booking code)\b\s*:?",
    re.IGNORECASE,
)
_REFERENCE_TOKEN = re.compile(r"\b(?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]{3,}\b")
_EMAIL_CODE = re.compile(r"\b(kode|code)\b[^.]{0,80}\be-?mail\b", re.IGNORECASE)
_STRIP_TAGS = ("script", "style", "noscript", "template", "input", "textarea", "select")


class ResponseTooLargeError(ValueError):
    pass


class ReviewOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    NEEDS_REVIEW = "NEEDS_REVIEW"


@dataclass(frozen=True)
class LineItem:
    name: str
    quantity: int | None
    amount: str | None


@dataclass(frozen=True)
class ReservationReview:
    outcome: ReviewOutcome
    title: str | None
    heading: str | None
    line_items: tuple[LineItem, ...]
    total: str | None
    pickup: str | None
    location: str | None
    reference: str | None
    mentions_email_code: bool
    reasons: tuple[str, ...]


def read_response_file(path: Path) -> str:
    if path.stat().st_size > MAX_HTML_BYTES:
        raise ResponseTooLargeError(f"{path.name} is larger than {MAX_HTML_BYTES} bytes")
    return path.read_bytes().decode("utf-8", errors="replace")


def _text(node: Tag) -> str:
    return " ".join(node.get_text(" ", strip=True).split())


def _amount(text: str) -> str | None:
    match = _AMOUNT.search(text)
    return re.sub(r"^Rp\s?", "Rp ", match.group()) if match else None


def _line_items(soup: BeautifulSoup) -> tuple[LineItem, ...]:
    items = []
    for row in soup.find_all(["tr", "li"]):
        text = _text(row)
        if "Rp" not in text or re.search(r"\btotal\b", text, re.IGNORECASE):
            continue
        quantity_match = _QUANTITY.search(text)
        quantity = None
        if quantity_match:
            quantity = int(quantity_match.group(1) or quantity_match.group(2))
        name = _AMOUNT.sub("", text)
        name = _QUANTITY.sub("", name) if quantity_match else name
        name = " ".join(name.split()).strip(" -\u2014\u2013\u00b7:")
        if name:
            items.append(LineItem(name=name, quantity=quantity, amount=_amount(text)))
    return tuple(items)


def _total(soup: BeautifulSoup) -> str | None:
    for string in soup.find_all(string=re.compile(r"\btotal\b", re.IGNORECASE)):
        element = string.parent
        for _ in range(3):
            if not isinstance(element, Tag):
                break
            amount = _amount(_text(element))
            if amount:
                return amount
            element = element.parent
    return None


def _labelled_value(soup: BeautifulSoup, label: re.Pattern[str], *, sibling: bool) -> str | None:
    for string in soup.find_all(string=label):
        assert isinstance(string, NavigableString)
        element = string.parent
        if not isinstance(element, Tag):
            continue
        text = _text(element)
        match = label.search(text)
        after = text[match.end() :].strip(" :") if match else ""
        if after:
            return after
        if sibling:
            following = element.find_next_sibling()
            if isinstance(following, Tag) and _text(following):
                return _text(following)
    return None


def _reference(soup: BeautifulSoup) -> str | None:
    value = _labelled_value(soup, _REFERENCE_LABEL, sibling=False)
    if value is None:
        return None
    token = _REFERENCE_TOKEN.search(value)
    return token.group() if token else None


def _outcome(title: str, heading: str, body: str) -> tuple[ReviewOutcome, list[str]]:
    lowered = f"{title} {body}".lower()
    if any(marker in lowered for marker in _CHALLENGE):
        return ReviewOutcome.NEEDS_REVIEW, [
            "looks like a security challenge page, not a reservation result"
        ]
    success = bool(_SUCCESS.search(heading) or _SUCCESS.search(title))
    failure = bool(_FAILURE.search(heading) or _FAILURE.search(title))
    if success and failure:
        return ReviewOutcome.NEEDS_REVIEW, ["conflicting success and failure indicators"]
    if _SUCCESS.search(heading):
        return ReviewOutcome.SUCCESS, []
    if failure:
        return ReviewOutcome.FAILURE, []
    if success:
        return ReviewOutcome.NEEDS_REVIEW, ["title says success but there is no success heading"]
    return ReviewOutcome.NEEDS_REVIEW, ["no recognisable success or failure indicator"]


def parse_reservation_response(html: str) -> ReservationReview:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(list(_STRIP_TAGS)):
        tag.decompose()

    title = " ".join(soup.title.get_text().split()) if soup.title else ""
    heading_tag = next((h for h in soup.find_all(["h1", "h2"]) if _text(h)), None)
    heading = _text(heading_tag) if heading_tag else ""
    body = _text(soup.body) if soup.body else _text(soup)
    outcome, reasons = _outcome(title, heading, body)

    return ReservationReview(
        outcome=outcome,
        title=title or None,
        heading=heading or None,
        line_items=_line_items(soup),
        total=_total(soup),
        pickup=_labelled_value(soup, _PICKUP_LABEL, sibling=True),
        location=_labelled_value(soup, _LOCATION_LABEL, sibling=True),
        reference=_reference(soup),
        mentions_email_code=bool(_EMAIL_CODE.search(body)),
        reasons=tuple(reasons),
    )


def compare_with_booking(
    review: ReservationReview, *, product_name: str, quantity: int
) -> list[str]:
    """Differences between the page and the local booking that the operator should check."""
    if not review.line_items:
        return ["No line items could be read from the page; check it manually."]
    wanted = product_name.casefold()
    item = next((i for i in review.line_items if wanted in i.name.casefold()), None)
    if item is None:
        return [f"{product_name} does not appear in the line items on the page."]
    if item.quantity is not None and item.quantity != quantity:
        return [
            f"Page shows quantity {item.quantity} for {product_name}; the booking has {quantity}."
        ]
    return []
