from datetime import UTC, datetime, timedelta
from pathlib import Path

from rich.console import Console

from app.clients.puln_client import LiveOk, ServerError
from app.models.puln import parse_live_status
from app.services.monitor_service import MonitorState, TimedEvent
from app.services.session_service import SessionRef
from app.services.stock_monitor import ChangeEvent, EventKind, Severity
from app.ui.dashboard import DashboardContext, Freshness, freshness, render_dashboard

SESSION = SessionRef(
    slug="online-order-20261031",
    page_url="https://puln.seatin.id/c/online-order-20261031",
    live_url="https://puln.seatin.id/c/online-order-20261031/live.json",
)
STATUS = parse_live_status((Path(__file__).parent / "fixtures" / "live_sample.json").read_bytes())
CHECKED = datetime(2026, 10, 30, 23, 7, tzinfo=UTC)
OPENS = datetime(2026, 10, 30, 23, 0, tzinfo=UTC)
CONTEXT = DashboardContext(
    timezone="Asia/Jakarta",
    target_ids=frozenset({100}),
    stale_after_seconds=30,
    max_customers=10,
    active_customers=2,
    prepared_bookings=1,
    confirmed_bookings=0,
)


def live_state(**overrides: object) -> MonitorState:
    state = MonitorState(
        session=SESSION,
        last_valid=STATUS,
        last_valid_at=CHECKED,
        last_result=LiveOk(
            http_status=200, duration_ms=120, status=STATUS, unknown_fields=frozenset()
        ),
        last_check_at=CHECKED,
        next_check_at=CHECKED + timedelta(seconds=10),
        product_names={100: "Kataifi Rolls"},
    )
    for name, value in overrides.items():
        setattr(state, name, value)
    return state


def render(state: MonitorState, now: datetime = CHECKED + timedelta(seconds=3)) -> str:
    console = Console(record=True, width=120, color_system=None)
    console.print(render_dashboard(state, CONTEXT, now))
    return console.export_text()


def test_header_shows_session_and_local_times() -> None:
    text = render(live_state())

    assert "online-order-20261031" in text
    assert "OPEN" in text
    assert "YES" in text
    assert "2026-10-31 06:07:00 WIB" in text  # server_now 23:07:00 UTC
    assert "2026-10-31 06:00:00 WIB" in text  # opens_at
    assert "Asia/Jakarta" in text


def test_products_show_names_stock_and_target_marker() -> None:
    text = render(live_state())

    kataifi = next(line for line in text.splitlines() if "Kataifi Rolls" in line and "│" in line)
    assert "100" in kataifi
    assert "60" in kataifi
    assert "*" in kataifi
    assert "Product #102" in text


def test_slots_show_labels_and_capacity() -> None:
    text = render(live_state())

    slot = next(line for line in text.splitlines() if " 185 " in line)
    assert "11:00" in slot
    assert "12:00" in slot
    assert "31" in slot


def test_customer_and_booking_counts() -> None:
    text = render(live_state())

    assert "2 / 10" in text
    assert "Prepared bookings: 1" in text
    assert "Confirmed bookings: 0" in text


def test_monitor_section_shows_last_response() -> None:
    text = render(live_state())

    assert "HTTP 200 in 120 ms" in text
    assert "2026-10-31 06:07:10 WIB" in text  # next check


def test_fresh_data_is_live() -> None:
    assert freshness(live_state(), CONTEXT, CHECKED + timedelta(seconds=3)) is Freshness.LIVE
    assert "LIVE" in render(live_state())


def test_old_data_is_stale() -> None:
    later = CHECKED + timedelta(seconds=45)

    assert freshness(live_state(), CONTEXT, later) is Freshness.STALE
    assert "STALE" in render(live_state(), now=later)


def test_failed_latest_check_shows_last_known_data() -> None:
    state = live_state(last_result=ServerError(http_status=503, duration_ms=40))

    text = render(state)

    assert freshness(state, CONTEXT, CHECKED) is Freshness.LAST_KNOWN
    assert "LAST KNOWN" in text
    assert "HTTP 503" in text
    assert "Kataifi Rolls" in text


def test_cached_data_from_previous_run_is_labelled() -> None:
    state = live_state(last_valid_is_cached=True, last_result=None, last_check_at=None)

    assert freshness(state, CONTEXT, CHECKED) is Freshness.CACHED
    assert "CACHED" in render(state)


def test_no_data_yet() -> None:
    state = MonitorState(session=SESSION)

    text = render(state)

    assert freshness(state, CONTEXT, CHECKED) is Freshness.NO_DATA
    assert "NO DATA" in text


def test_stop_reason_is_shown() -> None:
    state = live_state(stop_reason="session not found (HTTP 404)", next_check_at=None)

    text = render(state)

    assert "session not found (HTTP 404)" in text
    assert "stopped" in text.lower()


def test_recent_events_are_listed_newest_first() -> None:
    state = live_state()
    state.events.append(
        TimedEvent(CHECKED, ChangeEvent(EventKind.SLOT_CHANGED, Severity.INFO, "older event"))
    )
    state.events.append(
        TimedEvent(
            CHECKED + timedelta(seconds=10),
            ChangeEvent(EventKind.PRODUCT_RESTOCKED, Severity.ALERT, "newer event"),
        )
    )

    text = render(state)

    assert text.index("newer event") < text.index("older event")
    assert "06:07:10" in text


def test_failed_check_on_cached_data_reports_unavailability() -> None:
    state = live_state(
        last_valid_is_cached=True, last_result=ServerError(http_status=503, duration_ms=40)
    )

    assert freshness(state, CONTEXT, CHECKED) is Freshness.LAST_KNOWN
    assert "current data unavailable" in render(state)


def test_target_summary_shows_sold_out() -> None:
    text = render(live_state())

    first_lines = "\n".join(text.splitlines()[:4])
    assert "Kataifi Rolls: SOLD OUT" in first_lines


def test_target_summary_shows_available_stock() -> None:
    restocked = STATUS.model_copy(
        update={
            "products": [
                p.model_copy(update={"stock_remaining": 3}) if p.id == 100 else p
                for p in STATUS.products
            ]
        }
    )

    text = render(live_state(last_valid=restocked))

    assert "Kataifi Rolls: AVAILABLE 3" in text


def test_target_summary_flags_missing_target() -> None:
    without = STATUS.model_copy(update={"products": [p for p in STATUS.products if p.id != 100]})

    assert "Kataifi Rolls: NOT IN LIVE DATA" in render(live_state(last_valid=without))


def test_next_check_shows_countdown() -> None:
    text = render(live_state(), now=CHECKED + timedelta(seconds=3))

    assert "(in 7s)" in text


def test_pre_open_session_shows_time_until_opening() -> None:
    pre_open = STATUS.model_copy(
        update={"accepting": False, "server_now": OPENS - timedelta(minutes=12)}
    )
    now = OPENS - timedelta(minutes=12, seconds=30)

    text = render(live_state(last_valid=pre_open, last_valid_at=now), now=now)

    assert "Opens in 12m 30s" in text
