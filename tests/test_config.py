from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, load_settings


def test_defaults_are_conservative() -> None:
    settings = load_settings(env_file=None)

    assert settings.app_timezone == "Asia/Jakarta"
    assert settings.puln_base_url == "https://puln.seatin.id"
    assert settings.puln_poll_interval_seconds == 10
    assert settings.puln_max_poll_interval_seconds == 60
    assert settings.puln_request_timeout_seconds == 10
    assert settings.target_product_ids == [100]
    assert settings.max_customers == 10
    assert settings.puln_session_date is None
    assert settings.puln_session_slug is None
    assert settings.puln_session_url is None


def test_reads_values_from_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("PULN_SESSION_DATE=2026-11-07\nPULN_POLL_INTERVAL_SECONDS=15\n")

    settings = load_settings(env_file=env_file)

    assert settings.puln_session_date == date(2026, 11, 7)
    assert settings.puln_poll_interval_seconds == 15


def test_target_product_ids_accept_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TARGET_PRODUCT_IDS", "100, 102,97")

    assert load_settings(env_file=None).target_product_ids == [100, 102, 97]


def test_target_product_ids_reject_non_numeric(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TARGET_PRODUCT_IDS", "100,abc")

    with pytest.raises(ValidationError):
        load_settings(env_file=None)


def test_empty_optional_strings_become_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PULN_SESSION_SLUG", "")
    monkeypatch.setenv("PULN_SESSION_URL", "  ")
    monkeypatch.setenv("PULN_SESSION_DATE", "")

    settings = load_settings(env_file=None)

    assert settings.puln_session_slug is None
    assert settings.puln_session_url is None
    assert settings.puln_session_date is None


def test_rejects_invalid_session_date() -> None:
    with pytest.raises(ValidationError):
        Settings(puln_session_date="2026-13-40")  # type: ignore[arg-type]


@pytest.mark.parametrize("interval", [0, 1, 4.9])
def test_rejects_aggressive_poll_interval(interval: float) -> None:
    with pytest.raises(ValidationError):
        Settings(puln_poll_interval_seconds=interval)


def test_rejects_max_interval_below_interval() -> None:
    with pytest.raises(ValidationError):
        Settings(puln_poll_interval_seconds=30, puln_max_poll_interval_seconds=20)


def test_rejects_non_https_base_url() -> None:
    with pytest.raises(ValidationError):
        Settings(puln_base_url="http://puln.seatin.id")


def test_strips_trailing_slash_from_base_url() -> None:
    assert Settings(puln_base_url="https://puln.seatin.id/").puln_base_url == (
        "https://puln.seatin.id"
    )


def test_session_url_must_share_base_url_host() -> None:
    with pytest.raises(ValidationError):
        Settings(puln_session_url="https://example.com/c/online-order-20261031")


def test_accepts_session_url_on_base_host() -> None:
    url = "https://puln.seatin.id/c/online-order-20261031"

    assert Settings(puln_session_url=url).puln_session_url == url


@pytest.mark.parametrize("slug", ["Online Order", "../etc", "order/1", "ORDER"])
def test_rejects_unsafe_session_slug(slug: str) -> None:
    with pytest.raises(ValidationError):
        Settings(puln_session_slug=slug)


def test_rejects_unknown_timezone() -> None:
    with pytest.raises(ValidationError):
        Settings(app_timezone="Mars/Olympus")


@pytest.mark.parametrize("value", [0, 11])
def test_max_customers_is_between_one_and_ten(value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(max_customers=value)
