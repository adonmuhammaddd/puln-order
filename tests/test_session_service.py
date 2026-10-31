from datetime import date

import pytest

from app.config import Settings
from app.services.session_service import (
    SessionConfigError,
    SessionRef,
    resolve_session,
    slug_for_date,
)


def test_slug_for_date_uses_yyyymmdd() -> None:
    assert slug_for_date(date(2026, 11, 7)) == "online-order-20261107"


def test_resolves_from_session_date() -> None:
    ref = resolve_session(Settings(puln_session_date=date(2026, 10, 31)))

    assert ref == SessionRef(
        slug="online-order-20261031",
        page_url="https://puln.seatin.id/c/online-order-20261031",
        live_url="https://puln.seatin.id/c/online-order-20261031/live.json",
    )


def test_slug_overrides_date() -> None:
    settings = Settings(puln_session_date=date(2026, 10, 31), puln_session_slug="special-drop-1")

    ref = resolve_session(settings)

    assert ref.slug == "special-drop-1"
    assert ref.live_url == "https://puln.seatin.id/c/special-drop-1/live.json"


def test_url_overrides_slug_and_date() -> None:
    settings = Settings(
        puln_session_date=date(2026, 10, 31),
        puln_session_slug="ignored",
        puln_session_url="https://puln.seatin.id/c/online-order-20261114",
    )

    ref = resolve_session(settings)

    assert ref.slug == "online-order-20261114"
    assert ref.page_url == "https://puln.seatin.id/c/online-order-20261114"
    assert ref.live_url == "https://puln.seatin.id/c/online-order-20261114/live.json"


@pytest.mark.parametrize(
    "url",
    [
        "https://puln.seatin.id/c/online-order-20261114/",
        "https://puln.seatin.id/c/online-order-20261114/live.json",
        "https://puln.seatin.id/c/online-order-20261114?ref=wa#top",
    ],
)
def test_url_override_is_normalised(url: str) -> None:
    ref = resolve_session(Settings(puln_session_url=url))

    assert ref.page_url == "https://puln.seatin.id/c/online-order-20261114"


def test_url_override_without_session_path_is_rejected() -> None:
    with pytest.raises(SessionConfigError):
        resolve_session(Settings(puln_session_url="https://puln.seatin.id/"))


def test_missing_session_configuration_is_an_error() -> None:
    with pytest.raises(SessionConfigError, match="PULN_SESSION_DATE"):
        resolve_session(Settings())
