from types import SimpleNamespace

import pytest

from app.adapters.backend import PulnReadOnlyBackendAdapter
from app.adapters.browser import (
    BrowserAdapterError,
    BrowserStage,
    classify_visible_page,
    validate_official_url,
)
from app.clients.puln_client import LiveOk, PageCheck
from app.models.puln import LiveStatus
from app.services.session_service import SessionRef

SESSION = SessionRef(
    slug="online-order-20261101",
    page_url="https://puln.seatin.id/c/online-order-20261101",
    live_url="https://puln.seatin.id/c/online-order-20261101/live.json",
)


def test_backend_adapter_combines_page_and_validated_live_status() -> None:
    live = LiveOk(
        http_status=200,
        duration_ms=12,
        status=LiveStatus(status="open", accepting=True),
        unknown_fields=frozenset(),
    )
    client = SimpleNamespace(
        check_page=lambda url: PageCheck(True, 200, "session page found"),
        fetch_live=lambda url: live,
    )

    result = PulnReadOnlyBackendAdapter(client).inspect(SESSION)  # type: ignore[arg-type]

    assert result.page.reachable is True
    assert result.live is live
    assert result.accepting is True


def test_backend_adapter_does_not_guess_accepting_when_live_is_invalid() -> None:
    client = SimpleNamespace(
        check_page=lambda url: PageCheck(True, 200, "session page found"),
        fetch_live=lambda url: object(),
    )

    result = PulnReadOnlyBackendAdapter(client).inspect(SESSION)  # type: ignore[arg-type]

    assert result.accepting is None


@pytest.mark.parametrize(
    "copy",
    ["Verify you are human", "Checking your browser before accessing", "Just a moment"],
)
def test_browser_classifies_challenge_without_interacting(copy: str) -> None:
    assert classify_visible_page(copy) is BrowserStage.CHALLENGE


def test_browser_classifies_reservation_form() -> None:
    assert classify_visible_page("Reservasi Nama WhatsApp Email") is BrowserStage.RESERVATION_FORM


def test_browser_classifies_success_copy() -> None:
    assert classify_visible_page("Reservasi berhasil") is BrowserStage.SUCCESS_PAGE


def test_browser_classifies_failure_copy() -> None:
    assert classify_visible_page("Reservasi gagal") is BrowserStage.FAILURE_PAGE


def test_browser_unknown_copy_is_not_assumed_success() -> None:
    assert classify_visible_page("Terima kasih") is BrowserStage.UNKNOWN


def test_only_official_https_host_is_accepted() -> None:
    assert validate_official_url(SESSION.page_url) == SESSION.page_url
    for url in (
        "http://puln.seatin.id/c/session",
        "https://example.com/c/session",
        "https://puln.seatin.id.example.com/c/session",
        "https://user:pass@puln.seatin.id/c/session",
    ):
        with pytest.raises(BrowserAdapterError):
            validate_official_url(url)
