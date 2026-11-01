"""Monitoring workflow: fetch, persist, compare, notify, schedule (spec section 9.1)."""

import logging
import random
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.clients.puln_client import FetchResult, LiveOk, PulnClient
from app.models.puln import LiveStatus
from app.repositories import monitor_check_repository, product_repository, snapshot_repository
from app.services.poll_policy import PollDecision, PollTiming, decide_next
from app.services.session_service import SessionRef
from app.services.stock_monitor import (
    ChangeEvent,
    DriftTracker,
    EventKind,
    HealthTracker,
    Severity,
    diff_states,
)

logger = logging.getLogger(__name__)

EVENT_HISTORY = 50
_TICK_SECONDS = 1.0


@dataclass(frozen=True)
class TimedEvent:
    at: datetime
    event: ChangeEvent


@dataclass
class MonitorState:
    session: SessionRef
    last_valid: LiveStatus | None = None
    last_valid_at: datetime | None = None
    last_valid_is_cached: bool = False
    last_result: FetchResult | None = None
    last_check_at: datetime | None = None
    consecutive_failures: int = 0
    next_check_at: datetime | None = None
    stop_reason: str | None = None
    product_names: dict[int, str] = field(default_factory=dict)
    events: deque[TimedEvent] = field(default_factory=lambda: deque(maxlen=EVENT_HISTORY))


class MonitorService:
    def __init__(
        self,
        *,
        client: PulnClient,
        session_factory: sessionmaker[Session],
        session: SessionRef,
        target_ids: frozenset[int],
        timing: PollTiming,
        keep_running: bool,
        clock: Callable[[], datetime],
        rng: random.Random,
        on_event: Callable[[ChangeEvent], None],
        on_live: Callable[[LiveOk], None] | None = None,
    ) -> None:
        self._client = client
        self._session_factory = session_factory
        self._target_ids = target_ids
        self._timing = timing
        self._keep_running = keep_running
        self._clock = clock
        self._rng = rng
        self._on_event = on_event
        self._on_live = on_live
        self._health = HealthTracker()
        self._drift = DriftTracker()
        self._catalogue_loaded = False
        self._database_failing = False
        self.state = MonitorState(session=session)

    @property
    def target_ids(self) -> frozenset[int]:
        return self._target_ids

    def now(self) -> datetime:
        return self._clock()

    def _load_catalogue(self, db: Session) -> None:
        product_repository.seed_known_products(db)
        product_repository.sync_targets(db, self._target_ids)
        self.state.product_names = product_repository.product_names(db)
        self._catalogue_loaded = True

    def load_cached(self) -> None:
        """Show the last valid state from a previous run, marked as cached."""
        with self._session_factory() as db:
            self._load_catalogue(db)
            cached = snapshot_repository.load_last_state(db, self.state.session.slug)
            db.commit()
        if cached is not None:
            self.state.last_valid, self.state.last_valid_at = cached
            self.state.last_valid_is_cached = True

    def _emit(self, events: list[ChangeEvent], at: datetime) -> None:
        for event in events:
            logger.info("event %s: %s", event.kind, event.message)
            self.state.events.append(TimedEvent(at, event))
            self._on_event(event)

    def _persist(self, result: FetchResult, started_at: datetime) -> list[ChangeEvent]:
        slug = self.state.session.slug
        try:
            with self._session_factory() as db:
                if not self._catalogue_loaded:
                    self._load_catalogue(db)
                monitor_check_repository.record_check(db, slug, result, started_at)
                if isinstance(result, LiveOk):
                    snapshot_repository.record_live_state(db, slug, result.status, started_at)
                db.commit()
        except SQLAlchemyError as error:
            logger.exception("could not save monitoring data")
            if self._database_failing:
                return []
            self._database_failing = True
            return [
                ChangeEvent(
                    EventKind.DATABASE_ERROR,
                    Severity.ALERT,
                    f"Database error, history not saved ({type(error).__name__}); "
                    "monitoring continues",
                )
            ]
        self._database_failing = False
        return []

    def poll_once(self) -> PollDecision:
        state = self.state
        started_at = self._clock()
        result = self._client.fetch_live(state.session.live_url)
        state.last_result, state.last_check_at = result, started_at

        events: list[ChangeEvent] = []
        if isinstance(result, LiveOk):
            state.consecutive_failures = 0
        else:
            state.consecutive_failures += 1
        health = self._health.observe(result)
        if health is not None:
            events.append(health)

        # Persist first so product names are available for the change messages.
        events.extend(self._persist(result, started_at))

        if isinstance(result, LiveOk):
            drift = self._drift.observe(result.unknown_fields)
            if drift is not None:
                events.append(drift)
            events.extend(
                diff_states(
                    state.last_valid,
                    result.status,
                    target_ids=self._target_ids,
                    product_names=state.product_names,
                )
            )
            state.last_valid, state.last_valid_at = result.status, started_at
            state.last_valid_is_cached = False
            if self._on_live is not None:
                try:
                    self._on_live(result)
                except Exception:
                    logger.exception("live monitoring callback failed")

        decision = decide_next(
            result,
            consecutive_failures=state.consecutive_failures,
            timing=self._timing,
            now=started_at,
            keep_running=self._keep_running,
            rng=self._rng,
        )
        if decision.stop:
            state.next_check_at, state.stop_reason = None, decision.reason
            logger.info("monitoring stopped: %s", decision.reason)
        else:
            state.next_check_at = started_at + timedelta(seconds=decision.delay_seconds)
        self._emit(events, started_at)
        return decision


def run_monitor(
    service: MonitorService,
    *,
    sleep: Callable[[float], None],
    on_update: Callable[[MonitorState], None],
) -> str:
    """Poll until the policy says stop; returns the stop reason. Ctrl+C propagates."""
    while True:
        decision = service.poll_once()
        on_update(service.state)
        if decision.stop:
            return decision.reason
        next_check = service.state.next_check_at
        assert next_check is not None
        while (remaining := (next_check - service.now()).total_seconds()) > 0:
            sleep(min(_TICK_SECONDS, remaining))
            on_update(service.state)
