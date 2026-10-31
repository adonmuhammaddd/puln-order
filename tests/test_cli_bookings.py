from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app.main import main
from tests.test_cli_monitoring import JSON, SAMPLE

Feed = Callable[..., None]
PAGE_URL = "https://puln.seatin.id/c/online-order-20261031"


@pytest.fixture
def feed(monkeypatch: pytest.MonkeyPatch) -> Feed:
    def queue(*answers: str) -> None:
        pending = list(answers)

        def fake_input(prompt: str = "") -> str:
            if not pending:
                raise EOFError
            return pending.pop(0)

        monkeypatch.setattr("builtins.input", fake_input)

    return queue


@pytest.fixture
def opened() -> list[str]:
    return []


@pytest.fixture
def run(opened: list[str]) -> Callable[..., int]:
    def fake_open(url: str) -> bool:
        opened.append(url)
        return True

    def runner(*argv: str) -> int:
        return main(list(argv), open_url=fake_open)

    return runner


@pytest.fixture(autouse=True)
def prepared_environment(
    monkeypatch: pytest.MonkeyPatch, feed: Feed, capsys: pytest.CaptureFixture[str]
) -> None:
    """A configured session with live data and one consenting synthetic customer."""
    monkeypatch.setenv("PULN_SESSION_DATE", "2026-10-31")
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=SAMPLE, headers=JSON))
    main(["status"], transport=transport)
    feed("Synthetic Customer", "081200000001", "customer@example.test", "", "yes")
    main(["customers", "add"])
    capsys.readouterr()


def prepare(feed: Feed, run: Callable[..., int], *, save: str = "READY", qty: str = "1") -> int:
    feed("1", "100", qty, "185", "", save)
    return run("bookings", "prepare")


def test_prepare_shows_choices_warnings_and_saves_ready(
    feed: Feed, run: Callable[..., int], capsys: pytest.CaptureFixture[str]
) -> None:
    assert prepare(feed, run) == 0

    out = capsys.readouterr().out
    assert "Synthetic Customer" in out
    assert "customer@example.test" not in out
    assert "Kataifi Rolls" in out
    assert "11:00" in out
    assert "Availability can change at any time" in out
    assert "Last known stock is 0" in out
    assert "Booking 1 saved as READY" in out


def test_prepare_can_save_draft(
    feed: Feed, run: Callable[..., int], capsys: pytest.CaptureFixture[str]
) -> None:
    assert prepare(feed, run, save="DRAFT") == 0
    assert "saved as DRAFT" in capsys.readouterr().out


def test_prepare_can_be_cancelled_before_saving(
    feed: Feed, run: Callable[..., int], capsys: pytest.CaptureFixture[str]
) -> None:
    assert prepare(feed, run, save="") == 1
    capsys.readouterr()

    run("bookings", "list")
    assert "READY" not in capsys.readouterr().out


def test_prepare_rejects_quantity_over_limit(
    feed: Feed, run: Callable[..., int], capsys: pytest.CaptureFixture[str]
) -> None:
    assert prepare(feed, run, qty="2") == 1
    assert "limit of 1" in capsys.readouterr().err


def test_list_and_show_booking(
    feed: Feed, run: Callable[..., int], capsys: pytest.CaptureFixture[str]
) -> None:
    prepare(feed, run)
    capsys.readouterr()

    assert run("bookings", "list") == 0
    listing = capsys.readouterr().out
    assert "Kataifi Rolls" in listing
    assert "READY" in listing

    assert run("bookings", "show", "1") == 0
    detail = capsys.readouterr().out
    assert "Synthetic Customer" in detail
    assert "c***@example.test" in detail
    assert "-> READY" in detail


def test_open_launches_official_page_and_records_status(
    feed: Feed, run: Callable[..., int], opened: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    prepare(feed, run)
    capsys.readouterr()

    assert run("bookings", "open", "1") == 0

    out = capsys.readouterr().out
    assert opened == [PAGE_URL]
    assert "does not fill or submit" in out
    assert "OFFICIAL_FLOW_OPENED" in out


def test_open_refuses_draft(
    feed: Feed, run: Callable[..., int], opened: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    prepare(feed, run, save="DRAFT")

    assert run("bookings", "open", "1") == 1
    assert opened == []
    assert "READY" in capsys.readouterr().err


def test_open_again_does_not_change_status(
    feed: Feed, run: Callable[..., int], opened: list[str]
) -> None:
    prepare(feed, run)
    run("bookings", "open", "1")

    assert run("bookings", "open", "1") == 0
    assert opened == [PAGE_URL, PAGE_URL]


def test_full_manual_flow_to_confirmed(
    feed: Feed, run: Callable[..., int], capsys: pytest.CaptureFixture[str]
) -> None:
    prepare(feed, run)
    run("bookings", "open", "1")
    assert run("bookings", "mark", "1", "submitted") == 0
    assert run("bookings", "mark", "1", "awaiting-email") == 0
    feed("yes", "PULN-XYZ")

    assert run("bookings", "confirm", "1") == 0
    capsys.readouterr()

    run("bookings", "show", "1")
    out = capsys.readouterr().out
    assert "CONFIRMED" in out
    assert "PULN-XYZ" in out


def test_confirm_requires_typed_email_verification(
    feed: Feed, run: Callable[..., int], capsys: pytest.CaptureFixture[str]
) -> None:
    prepare(feed, run)
    run("bookings", "open", "1")
    run("bookings", "mark", "1", "submitted")
    run("bookings", "mark", "1", "awaiting-email")
    feed("not yet")

    assert run("bookings", "confirm", "1") == 1
    capsys.readouterr()
    run("bookings", "show", "1")
    assert "Status: AWAITING_EMAIL" in capsys.readouterr().out


def test_mark_refuses_invalid_transition(
    feed: Feed, run: Callable[..., int], capsys: pytest.CaptureFixture[str]
) -> None:
    prepare(feed, run)

    assert run("bookings", "mark", "1", "awaiting-email") == 1
    assert "READY -> AWAITING_EMAIL" in capsys.readouterr().err


def test_mark_with_note_prompts_for_it(
    feed: Feed, run: Callable[..., int], capsys: pytest.CaptureFixture[str]
) -> None:
    prepare(feed, run)
    feed("customer will retry tomorrow")

    assert run("bookings", "mark", "1", "cancelled", "--note") == 0
    capsys.readouterr()
    run("bookings", "show", "1")
    assert "customer will retry tomorrow" in capsys.readouterr().out


def test_mark_cannot_confirm(run: Callable[..., int]) -> None:
    with pytest.raises(SystemExit):
        run("bookings", "mark", "1", "confirmed")


def test_products_limit(run: Callable[..., int], capsys: pytest.CaptureFixture[str]) -> None:
    assert run("products", "limit", "100", "2") == 0
    assert run("products", "limit", "100", "none") == 0
    assert run("products", "limit", "555", "1") == 2
    assert "products name" in capsys.readouterr().err


def test_dashboard_counts_prepared_bookings(
    feed: Feed, run: Callable[..., int], capsys: pytest.CaptureFixture[str]
) -> None:
    prepare(feed, run)
    capsys.readouterr()
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=SAMPLE, headers=JSON))

    main(["status"], transport=transport)

    assert "Prepared bookings: 1" in capsys.readouterr().out


RESPONSES = Path(__file__).parent / "fixtures" / "responses"


def to_submitted(feed: Feed, run: Callable[..., int]) -> None:
    prepare(feed, run)
    run("bookings", "open", "1")
    run("bookings", "mark", "1", "submitted")


def test_review_success_page_records_review_after_confirmation(
    feed: Feed, run: Callable[..., int], capsys: pytest.CaptureFixture[str]
) -> None:
    to_submitted(feed, run)
    capsys.readouterr()
    feed("yes")

    assert run("bookings", "review", "1", str(RESPONSES / "success.html")) == 0

    out = capsys.readouterr().out
    assert "Outcome: SUCCESS" in out
    assert "Kataifi Rolls" in out
    assert "Rp 45.000" in out
    assert "not a confirmation" in out
    assert "Booking 1 is SUCCESS_PAGE_REVIEWED" in out
    assert "FAKE-TURNSTILE-TOKEN" not in out
    assert "Contoh Pelanggan" not in out


def test_review_warns_about_pickup_mismatch(
    feed: Feed, run: Callable[..., int], capsys: pytest.CaptureFixture[str]
) -> None:
    to_submitted(feed, run)
    capsys.readouterr()
    feed("no")

    run("bookings", "review", "1", str(RESPONSES / "success.html"))

    out = capsys.readouterr().out
    assert "pickup on the page differs" in out
    assert "Booking 1 is SUBMITTED_BY_USER" not in out


def test_review_without_operator_agreement_changes_nothing(
    feed: Feed, run: Callable[..., int], capsys: pytest.CaptureFixture[str]
) -> None:
    to_submitted(feed, run)
    feed("no")

    assert run("bookings", "review", "1", str(RESPONSES / "success.html")) == 1
    capsys.readouterr()
    run("bookings", "show", "1")
    assert "Status: SUBMITTED_BY_USER" in capsys.readouterr().out


def test_review_ambiguous_page_asks_for_manual_review(
    feed: Feed, run: Callable[..., int], capsys: pytest.CaptureFixture[str]
) -> None:
    to_submitted(feed, run)
    capsys.readouterr()

    assert run("bookings", "review", "1", str(RESPONSES / "challenge.html")) == 1
    assert "manual review" in capsys.readouterr().out.lower()


def test_review_failure_page_can_mark_failed(
    feed: Feed, run: Callable[..., int], capsys: pytest.CaptureFixture[str]
) -> None:
    to_submitted(feed, run)
    capsys.readouterr()
    feed("yes")

    assert run("bookings", "review", "1", str(RESPONSES / "failure.html")) == 0
    assert "Booking 1 is FAILED" in capsys.readouterr().out


def test_review_missing_file(run: Callable[..., int], capsys: pytest.CaptureFixture[str]) -> None:
    assert run("bookings", "review", "1", "missing.html") == 2
    assert "missing.html" in capsys.readouterr().err
