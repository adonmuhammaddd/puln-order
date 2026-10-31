from collections.abc import Callable

import httpx
import pytest

from app.main import main
from tests.test_cli_monitoring import JSON, SAMPLE

Feed = Callable[..., None]


@pytest.fixture
def feed(monkeypatch: pytest.MonkeyPatch) -> Feed:
    """Queue answers for interactive prompts; running out behaves like Ctrl+D."""

    def queue(*answers: str) -> None:
        pending = list(answers)

        def fake_input(prompt: str = "") -> str:
            if not pending:
                raise EOFError
            return pending.pop(0)

        monkeypatch.setattr("builtins.input", fake_input)

    return queue


def add(feed: Feed, n: int = 1, consent: str = "yes") -> int:
    feed(f"Synthetic Customer {n}", f"0812-0000-{n:04d}", f"customer{n}@example.test", "", consent)
    return main(["customers", "add"])


def test_add_with_consent_stores_customer(feed: Feed, capsys: pytest.CaptureFixture[str]) -> None:
    assert add(feed) == 0

    out = capsys.readouterr().out
    assert "Added customer 1" in out
    assert "customer1@example.test" not in out


def test_add_without_consent_is_cancelled(feed: Feed, capsys: pytest.CaptureFixture[str]) -> None:
    assert add(feed, consent="no") == 1
    capsys.readouterr()

    main(["customers", "list"])

    assert "Synthetic Customer 1" not in capsys.readouterr().out


def test_add_reprompts_invalid_values(feed: Feed, capsys: pytest.CaptureFixture[str]) -> None:
    feed("Synthetic Customer", "12345", "081200000001", "not-an-email", "a@example.test", "", "yes")

    assert main(["customers", "add"]) == 0

    out = capsys.readouterr().out
    assert "WhatsApp number" in out
    assert "email address is not valid" in out


def test_add_gives_up_after_repeated_invalid_values(feed: Feed) -> None:
    feed("Synthetic Customer", "1", "2", "3")

    assert main(["customers", "add"]) == 2


def test_add_is_cancelled_by_end_of_input(feed: Feed, capsys: pytest.CaptureFixture[str]) -> None:
    feed("Synthetic Customer")

    assert main(["customers", "add"]) == 1
    assert "Cancelled" in capsys.readouterr().out


def test_add_refuses_beyond_maximum(
    feed: Feed, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MAX_CUSTOMERS", "1")
    add(feed, 1)

    assert add(feed, 2) == 1
    assert "already 1 active customers" in capsys.readouterr().err


def test_list_masks_contact_details(feed: Feed, capsys: pytest.CaptureFixture[str]) -> None:
    add(feed)
    capsys.readouterr()

    assert main(["customers", "list"]) == 0

    out = capsys.readouterr().out
    assert "Synthetic Customer 1" in out
    assert "+62********001" in out
    assert "c***@example.test" in out
    assert "customer1@example.test" not in out
    assert "Active profiles: 1 / 10" in out


def test_show_masks_by_default_and_reveals_on_request(
    feed: Feed, capsys: pytest.CaptureFixture[str]
) -> None:
    add(feed)
    capsys.readouterr()

    main(["customers", "show", "1"])
    masked = capsys.readouterr().out
    main(["customers", "show", "1", "--reveal"])
    revealed = capsys.readouterr().out

    assert "customer1@example.test" not in masked
    assert "c***@example.test" in masked
    assert "customer1@example.test" in revealed
    assert "+6281200000001" in revealed


def test_show_unknown_customer(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["customers", "show", "7"]) == 1
    assert "customer 7 does not exist" in capsys.readouterr().err


def test_edit_keeps_fields_left_blank(feed: Feed, capsys: pytest.CaptureFixture[str]) -> None:
    add(feed)
    feed("", "", "new.address@example.test", "")

    assert main(["customers", "edit", "1"]) == 0
    capsys.readouterr()

    main(["customers", "show", "1", "--reveal"])
    out = capsys.readouterr().out
    assert "Synthetic Customer 1" in out
    assert "+6281200000001" in out
    assert "new.address@example.test" in out


def test_edit_can_clear_notes(feed: Feed, capsys: pytest.CaptureFixture[str]) -> None:
    feed("Synthetic Customer 1", "081200000001", "c1@example.test", "prefers noon", "yes")
    main(["customers", "add"])
    feed("", "", "", "-")

    main(["customers", "edit", "1"])
    capsys.readouterr()

    main(["customers", "show", "1"])
    assert "prefers noon" not in capsys.readouterr().out


def test_deactivate_and_activate(feed: Feed, capsys: pytest.CaptureFixture[str]) -> None:
    add(feed)

    assert main(["customers", "deactivate", "1"]) == 0
    capsys.readouterr()
    main(["customers", "list"])
    assert "Active profiles: 0 / 10" in capsys.readouterr().out

    assert main(["customers", "activate", "1"]) == 0
    capsys.readouterr()
    main(["customers", "list"])
    assert "Active profiles: 1 / 10" in capsys.readouterr().out


def test_delete_requires_typed_confirmation(feed: Feed, capsys: pytest.CaptureFixture[str]) -> None:
    add(feed)
    feed("nope")

    assert main(["customers", "delete", "1"]) == 1
    capsys.readouterr()
    main(["customers", "list"])
    assert "Synthetic Customer 1" in capsys.readouterr().out


def test_delete_with_confirmation(feed: Feed, capsys: pytest.CaptureFixture[str]) -> None:
    add(feed)
    feed("DELETE")

    assert main(["customers", "delete", "1"]) == 0
    capsys.readouterr()
    main(["customers", "list"])
    assert "Synthetic Customer 1" not in capsys.readouterr().out


def test_delete_with_yes_flag_skips_prompt(feed: Feed) -> None:
    add(feed)
    feed()

    assert main(["customers", "delete", "1", "--yes"]) == 0


def test_anonymize_erases_contact_details(feed: Feed, capsys: pytest.CaptureFixture[str]) -> None:
    add(feed)

    assert main(["customers", "delete", "1", "--anonymize", "--yes"]) == 0
    capsys.readouterr()
    main(["customers", "show", "1", "--reveal"])

    out = capsys.readouterr().out
    assert "Deleted customer #1" in out
    assert "customer1@example.test" not in out


def test_dashboard_counts_active_customers(
    feed: Feed, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    add(feed)
    capsys.readouterr()
    monkeypatch.setenv("PULN_SESSION_DATE", "2026-10-31")
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=SAMPLE, headers=JSON))

    main(["status"], transport=transport)

    out = capsys.readouterr().out
    assert "Active profiles: 1 / 10" in out
    assert "Synthetic Customer" not in out
    assert "example.test" not in out
