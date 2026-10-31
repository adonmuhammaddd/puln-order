import random
from datetime import UTC, datetime, timedelta

import pytest

from app.clients.puln_client import (
    FetchResult,
    Forbidden,
    InvalidPayload,
    LiveOk,
    NetworkError,
    NotFound,
    RateLimited,
    ServerError,
    UnexpectedStatus,
)
from app.models.puln import LiveStatus
from app.services.poll_policy import PollTiming, SessionPhase, decide_next, session_phase

OPENS = datetime(2026, 10, 30, 23, 0, tzinfo=UTC)
CLOSES = datetime(2026, 10, 31, 8, 30, tzinfo=UTC)
NO_JITTER = PollTiming(interval=10, max_interval=60, jitter_ratio=0)


def live(accepting: bool, server_now: datetime | None, status: str = "OPEN") -> LiveOk:
    payload = LiveStatus(
        status=status, accepting=accepting, opens_at=OPENS, closes_at=CLOSES, server_now=server_now
    )
    return LiveOk(http_status=200, duration_ms=100, status=payload, unknown_fields=frozenset())


def decide(
    result: FetchResult,
    *,
    failures: int = 0,
    timing: PollTiming = NO_JITTER,
    keep_running: bool = False,
    now: datetime = OPENS + timedelta(hours=1),
    seed: int = 1,
) -> tuple[float, bool, str]:
    decision = decide_next(
        result,
        consecutive_failures=failures,
        timing=timing,
        now=now,
        keep_running=keep_running,
        rng=random.Random(seed),
    )
    return decision.delay_seconds, decision.stop, decision.reason


def test_open_session_polls_at_interval() -> None:
    delay, stop, _ = decide(live(True, OPENS + timedelta(minutes=5)))

    assert (delay, stop) == (10, False)


@pytest.mark.parametrize("seed", range(50))
def test_jitter_stays_small_and_above_minimum(seed: int) -> None:
    timing = PollTiming(interval=10, max_interval=60, jitter_ratio=0.1)

    delay, _, _ = decide(live(True, OPENS + timedelta(minutes=5)), timing=timing, seed=seed)

    assert 9 <= delay <= 11


def test_jitter_never_goes_below_minimum_interval() -> None:
    timing = PollTiming(interval=5, max_interval=60, jitter_ratio=0.5)

    delays = [decide(live(True, OPENS), timing=timing, seed=s)[0] for s in range(50)]

    assert min(delays) >= 5


def test_phase_is_pre_open_before_opening_time() -> None:
    status = live(False, OPENS - timedelta(minutes=30)).status

    assert session_phase(status, now=OPENS) is SessionPhase.PRE_OPEN


def test_phase_uses_local_clock_when_server_time_missing() -> None:
    status = live(False, None).status

    assert session_phase(status, now=OPENS - timedelta(minutes=1)) is SessionPhase.PRE_OPEN
    assert session_phase(status, now=OPENS + timedelta(minutes=1)) is SessionPhase.CLOSED


def test_phase_is_open_while_accepting() -> None:
    assert session_phase(live(True, CLOSES).status, now=CLOSES) is SessionPhase.OPEN


def test_pre_open_polls_slowly_until_opening() -> None:
    delay, stop, reason = decide(live(False, OPENS - timedelta(minutes=30)))

    assert (delay, stop) == (60, False)
    assert "not open yet" in reason


def test_pre_open_wakes_up_at_opening_time() -> None:
    delay, _, _ = decide(live(False, OPENS - timedelta(seconds=25)))

    assert delay == 25


def test_pre_open_never_polls_faster_than_interval() -> None:
    delay, _, _ = decide(live(False, OPENS - timedelta(seconds=2)))

    assert delay == 10


def test_closed_session_stops_polling() -> None:
    delay, stop, reason = decide(live(False, CLOSES + timedelta(minutes=1), status="CLOSED"))

    assert stop is True
    assert delay == 0
    assert "no longer accepting" in reason


def test_closed_session_continues_slowly_with_operator_override() -> None:
    delay, stop, _ = decide(live(False, CLOSES + timedelta(minutes=1)), keep_running=True)

    assert (delay, stop) == (60, False)


@pytest.mark.parametrize(
    ("failures", "expected"),
    [(1, 20), (2, 40), (3, 60), (8, 60)],
)
def test_transient_failures_back_off_exponentially_up_to_maximum(
    failures: int, expected: float
) -> None:
    result = NetworkError(duration_ms=10_000, kind="timeout", message="ReadTimeout")

    delay, stop, _ = decide(result, failures=failures)

    assert (delay, stop) == (expected, False)


@pytest.mark.parametrize(
    "result",
    [
        ServerError(http_status=503, duration_ms=50),
        InvalidPayload(http_status=200, duration_ms=50, reason="bad"),
        NetworkError(duration_ms=5, kind="connect", message="ConnectError"),
    ],
)
def test_all_transient_failures_back_off(result: FetchResult) -> None:
    delay, stop, _ = decide(result, failures=1)

    assert (delay, stop) == (20, False)


def test_rate_limit_respects_retry_after() -> None:
    delay, stop, reason = decide(
        RateLimited(http_status=429, duration_ms=5, retry_after_seconds=300), failures=1
    )

    assert (delay, stop) == (300, False)
    assert "429" in reason


def test_rate_limit_waits_at_least_backoff_when_retry_after_is_short() -> None:
    delay, _, _ = decide(
        RateLimited(http_status=429, duration_ms=5, retry_after_seconds=1), failures=2
    )

    assert delay == 40


def test_rate_limit_without_retry_after_pauses_for_maximum_interval() -> None:
    delay, _, _ = decide(
        RateLimited(http_status=429, duration_ms=5, retry_after_seconds=None), failures=1
    )

    assert delay == 60


@pytest.mark.parametrize("seed", range(20))
def test_rate_limit_jitter_never_shortens_retry_after(seed: int) -> None:
    timing = PollTiming(interval=10, max_interval=60, jitter_ratio=0.1)

    delay, _, _ = decide(
        RateLimited(http_status=429, duration_ms=5, retry_after_seconds=120),
        failures=1,
        timing=timing,
        seed=seed,
    )

    assert delay >= 120


@pytest.mark.parametrize(
    ("result", "phrase"),
    [
        (NotFound(http_status=404, duration_ms=5), "not found"),
        (Forbidden(http_status=403, duration_ms=5, challenge=True), "browser"),
        (Forbidden(http_status=403, duration_ms=5, challenge=False), "403"),
        (UnexpectedStatus(http_status=302, duration_ms=5), "302"),
    ],
)
def test_blocking_responses_stop_even_with_override(result: FetchResult, phrase: str) -> None:
    _, stop, reason = decide(result, failures=1, keep_running=True)

    assert stop is True
    assert phrase in reason
