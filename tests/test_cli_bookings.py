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
def fake_open(opened: list[str]) -> Callable[[str], bool]:
    def record(url: str) -> bool:
        opened.append(url)
        return True

    return record


@pytest.fixture
def run(fake_open: Callable[[str], bool]) -> Callable[..., int]:
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


def test_open_warns_when_session_is_not_accepting(
    feed: Feed, run: Callable[..., int], opened: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    prepare(feed, run)
    closed = SAMPLE.replace(b'"accepting": true', b'"accepting": false')
    main(
        ["status"],
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=closed, headers=JSON)),
    )
    capsys.readouterr()

    assert run("bookings", "open", "1") == 0

    assert "not accepting orders" in capsys.readouterr().out
    assert opened == [PAGE_URL]


def add_second_customer(feed: Feed) -> None:
    feed("Second Customer", "081200000002", "second@example.test", "", "yes")
    main(["customers", "add"])


def test_open_all_ready_opens_a_tab_per_ready_booking(
    feed: Feed, run: Callable[..., int], opened: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    add_second_customer(feed)
    prepare(feed, run)
    feed("2", "100", "1", "185", "", "READY")
    run("bookings", "prepare")
    capsys.readouterr()

    assert run("bookings", "open", "--all-ready") == 0

    out = capsys.readouterr().out
    assert opened == [PAGE_URL, PAGE_URL]
    assert "Synthetic Customer" in out
    assert "Second Customer" in out
    assert "second@example.test" not in out
    assert "does not fill or submit" in out
    run("bookings", "list")
    assert capsys.readouterr().out.count("OFFICIAL_FLOW_OPENED") == 2


def test_open_all_ready_without_ready_bookings(
    feed: Feed, run: Callable[..., int], opened: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    prepare(feed, run, save="DRAFT")

    assert run("bookings", "open", "--all-ready") == 1
    assert opened == []
    assert "No READY bookings" in capsys.readouterr().out


def test_open_all_ready_reports_missing_browser(
    feed: Feed, run: Callable[..., int], capsys: pytest.CaptureFixture[str]
) -> None:
    prepare(feed, run)
    capsys.readouterr()

    assert main(["bookings", "open", "--all-ready"], open_url=lambda url: False) == 1
    assert PAGE_URL in capsys.readouterr().out
    run("bookings", "list")
    assert "READY" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["bookings", "open"], ["bookings", "open", "1", "--all-ready"]])
def test_open_needs_exactly_one_of_id_or_all_ready(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(argv)

    assert exc_info.value.code == 2


def stop_on_first_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupt(seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("app.ui.commands.time.sleep", interrupt)


def test_monitor_auto_open_opens_ready_bookings_when_session_opens(
    feed: Feed,
    run: Callable[..., int],
    fake_open: Callable[[str], bool],
    opened: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prepare(feed, run)
    before_opening = SAMPLE.replace(b'"accepting": true', b'"accepting": false')
    main(
        ["status"],
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=before_opening, headers=JSON)
        ),
    )
    stop_on_first_sleep(monkeypatch)
    capsys.readouterr()

    exit_code = main(
        ["monitor", "--no-bell", "--auto-open"],
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=SAMPLE, headers=JSON)),
        open_url=fake_open,
    )

    assert exit_code == 0
    assert opened == [PAGE_URL]
    assert "Auto-open" in capsys.readouterr().out
    run("bookings", "list")
    assert "OFFICIAL_FLOW_OPENED" in capsys.readouterr().out


def test_monitor_without_auto_open_never_opens_the_browser(
    feed: Feed,
    run: Callable[..., int],
    fake_open: Callable[[str], bool],
    opened: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepare(feed, run)
    before_opening = SAMPLE.replace(b'"accepting": true', b'"accepting": false')
    main(
        ["status"],
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=before_opening, headers=JSON)
        ),
    )
    stop_on_first_sleep(monkeypatch)

    main(
        ["monitor", "--no-bell"],
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=SAMPLE, headers=JSON)),
        open_url=fake_open,
    )

    assert opened == []
