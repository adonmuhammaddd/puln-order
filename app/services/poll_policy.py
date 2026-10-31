"""When to poll next. Pure functions, so every timing rule is unit-tested.

Rules: conservative fixed interval, small jitter only to avoid synchronised requests,
exponential backoff on transient failures, Retry-After always honoured, and a full stop on
responses that suggest we are not welcome (403, redirects) or the session is gone or closed.
"""

import random
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

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
from app.constants import MIN_POLL_INTERVAL_SECONDS
from app.models.puln import LiveStatus


class SessionPhase(StrEnum):
    PRE_OPEN = "pre_open"
    OPEN = "open"
    CLOSED = "closed"


@dataclass(frozen=True)
class PollTiming:
    interval: float
    max_interval: float
    jitter_ratio: float = 0.1


@dataclass(frozen=True)
class PollDecision:
    delay_seconds: float
    stop: bool
    reason: str


def session_phase(status: LiveStatus, now: datetime) -> SessionPhase:
    """Classify the session, preferring the server clock over the local one."""
    if status.accepting:
        return SessionPhase.OPEN
    reference = status.server_now or now
    if status.opens_at is not None and reference < status.opens_at:
        return SessionPhase.PRE_OPEN
    return SessionPhase.CLOSED


def _backoff(timing: PollTiming, failures: int) -> float:
    return min(timing.max_interval, timing.interval * 2.0 ** max(failures, 1))


def _jittered(delay: float, timing: PollTiming, rng: random.Random) -> float:
    factor = rng.uniform(1 - timing.jitter_ratio, 1 + timing.jitter_ratio)
    return max(float(MIN_POLL_INTERVAL_SECONDS), delay * factor)


def _stop(reason: str) -> PollDecision:
    return PollDecision(delay_seconds=0, stop=True, reason=reason)


def _decide_live(
    result: LiveOk, timing: PollTiming, now: datetime, keep_running: bool, rng: random.Random
) -> PollDecision:
    status = result.status
    phase = session_phase(status, now)
    if phase is SessionPhase.OPEN:
        return PollDecision(_jittered(timing.interval, timing, rng), False, "session open")
    if phase is SessionPhase.PRE_OPEN:
        assert status.opens_at is not None
        until_open = (status.opens_at - (status.server_now or now)).total_seconds()
        delay = min(timing.max_interval, max(timing.interval, until_open))
        return PollDecision(_jittered(delay, timing, rng), False, "session not open yet")
    if keep_running:
        return PollDecision(
            _jittered(timing.max_interval, timing, rng),
            False,
            "session not accepting orders; operator override keeps polling slowly",
        )
    return _stop(f"session no longer accepting orders (status {status.status})")


def decide_next(
    result: FetchResult,
    *,
    consecutive_failures: int,
    timing: PollTiming,
    now: datetime,
    keep_running: bool,
    rng: random.Random,
) -> PollDecision:
    """Decide the next poll. ``consecutive_failures`` includes ``result`` when it failed."""
    match result:
        case LiveOk():
            return _decide_live(result, timing, now, keep_running, rng)
        case RateLimited(retry_after_seconds=retry_after):
            base = (
                timing.max_interval
                if retry_after is None
                else max(retry_after, _backoff(timing, consecutive_failures))
            )
            delay = base * rng.uniform(1, 1 + timing.jitter_ratio)
            return PollDecision(delay, False, f"rate limited (HTTP 429); pausing {delay:.0f}s")
        case NotFound():
            return _stop("session not found (HTTP 404); check the session date or URL")
        case Forbidden(challenge=True):
            return _stop(
                "access challenged (HTTP 403); open the session in a browser, no automatic retry"
            )
        case Forbidden():
            return _stop("access denied (HTTP 403); no automatic retry")
        case UnexpectedStatus(http_status=code):
            return _stop(f"unexpected HTTP {code}; check the session page in a browser")
        case ServerError() | InvalidPayload() | NetworkError():
            delay = _jittered(_backoff(timing, consecutive_failures), timing, rng)
            return PollDecision(delay, False, f"{result.outcome}; retrying in {delay:.0f}s")
