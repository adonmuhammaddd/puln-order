import logging
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.logging_setup import LOG_FILE_NAME, configure_logging, redact


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in root.handlers:
        if handler not in handlers:
            handler.close()
    root.handlers, root.level = handlers, level


@pytest.mark.parametrize(
    "text",
    [
        "customer budi.santoso@example.com added",
        "email=SITI+promo@mail.example.co.id",
    ],
)
def test_redacts_email_addresses(text: str) -> None:
    result = redact(text)

    assert "@" not in result
    assert "[redacted-email]" in result


@pytest.mark.parametrize(
    "phone",
    ["081234567890", "+6281234567890", "6281234567890", "0812-3456-7890", "+62 812 3456 7890"],
)
def test_redacts_indonesian_phone_numbers(phone: str) -> None:
    result = redact(f"whatsapp {phone} saved")

    assert result == "whatsapp [redacted-phone] saved"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("cf-turnstile-response=0.AbCdEf123", "cf-turnstile-response=[redacted-secret]"),
        ("submission_key=9f8e7d6c&qty_100=1", "submission_key=[redacted-secret]&qty_100=1"),
        ("Cookie: __cf_bm=abc123; other=1", "Cookie: [redacted-secret]"),
        ("set-cookie: __cf_bm=abc123; path=/", "set-cookie: [redacted-secret]"),
        ("Authorization: Bearer abc.def.ghi", "Authorization: [redacted-secret]"),
    ],
)
def test_redacts_secret_values(text: str, expected: str) -> None:
    assert redact(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "GET https://puln.seatin.id/c/online-order-20261031/live.json -> HTTP 200 in 0.333s",
        "server_now=2026-10-31T05:35:53.101Z product 100 remaining 0 allocated 60",
    ],
)
def test_leaves_operational_details_untouched(text: str) -> None:
    assert redact(text) == text


def test_configure_logging_writes_redacted_records_to_file(tmp_path: Path) -> None:
    configure_logging(log_dir=tmp_path, level="INFO")

    logging.getLogger("app.test").info("added %s with %s", "a@example.com", "081234567890")
    for handler in logging.getLogger().handlers:
        handler.flush()

    content = (tmp_path / LOG_FILE_NAME).read_text()
    assert "added [redacted-email] with [redacted-phone]" in content
    assert "a@example.com" not in content


def test_configure_logging_is_idempotent(tmp_path: Path) -> None:
    configure_logging(log_dir=tmp_path, level="INFO")
    count = len(logging.getLogger().handlers)

    configure_logging(log_dir=tmp_path, level="INFO")

    assert len(logging.getLogger().handlers) == count


def test_configure_logging_does_not_log_to_console(tmp_path: Path) -> None:
    configure_logging(log_dir=tmp_path, level="INFO")

    stream_handlers = [
        handler
        for handler in logging.getLogger().handlers
        if type(handler) is logging.StreamHandler
    ]
    assert stream_handlers == []


def test_log_file_is_readable_by_owner_only(tmp_path: Path) -> None:
    configure_logging(log_dir=tmp_path, level="INFO")

    assert stat.S_IMODE((tmp_path / LOG_FILE_NAME).stat().st_mode) == 0o600
