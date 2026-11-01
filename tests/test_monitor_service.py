import json
import random
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.clients.puln_client import PulnClient, create_http_client
from app.database.engine import create_session_factory, init_db
from app.database.tables import MonitorCheck, StockSnapshot
from app.repositories import snapshot_repository
from app.services.monitor_service import MonitorService, run_monitor
from app.services.poll_policy import PollTiming
from app.services.session_service import SessionRef
from app.services.stock_monitor import ChangeEvent, EventKind, Severity

SESSION = SessionRef(
    slug="online-order-20261031",
    page_url="https://puln.seatin.id/c/online-order-20261031",
    live_url="https://puln.seatin.id/c/online-order-20261031/live.json",
)
SAMPLE: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "live_sample.json").read_text()
)
START = datetime(2026, 10, 30, 23, 7, tzinfo=UTC)


def body(kataifi: int = 0, **changes: Any) -> bytes:
    payload = json.loads(json.dumps(SAMPLE))
    for product in payload["products"]:
        if product["id"] == 100:
            product["stock_remaining"] = kataifi
    payload.update(changes)
    return json.dumps(payload).encode()


class Script:
    """Serves queued responses in order and records each request."""

    def __init__(self, *responses: httpx.Response) -> None:
        self._responses: Iterator[httpx.Response] = iter(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return next(self._responses)


def ok(content: bytes) -> httpx.Response:
    return httpx.Response(200, content=content, headers={"content-type": "application/json"})


class FakeClock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    return create_session_factory(init_db("sqlite://"))


def make_service(
    script: Callable[[httpx.Request], httpx.Response],
    session_factory: sessionmaker[Session],
    clock: FakeClock | None = None,
    events: list[ChangeEvent] | None = None,
    on_live: Callable | None = None,
) -> MonitorService:
    clock = clock or FakeClock()
    http = create_http_client(timeout=5, transport=httpx.MockTransport(script))
    sink = events if events is not None else []
    return MonitorService(
        client=PulnClient(http, clock=clock),
        session_factory=session_factory,
        session=SESSION,
        target_ids=frozenset({100}),
        timing=PollTiming(interval=10, max_interval=60, jitter_ratio=0),
        keep_running=False,
        clock=clock,
        rng=random.Random(0),
        on_event=sink.append,
        on_live=on_live,
    )


def test_first_poll_establishes_baseline(session_factory: sessionmaker[Session]) -> None:
    events: list[ChangeEvent] = []
    service = make_service(Script(ok(body())), session_factory, events=events)

    decision = service.poll_once()

    assert decision.stop is False
    assert decision.delay_seconds == 10
    assert events == []
    assert service.state.last_valid is not None
    assert service.state.last_valid_is_cached is False
    assert service.state.last_valid_at == START
    assert service.state.next_check_at == START + timedelta(seconds=10)
    with session_factory() as db:
        assert len(db.scalars(select(StockSnapshot)).all()) == 6
        assert db.scalars(select(MonitorCheck)).one().outcome == "ok"


def test_change_between_polls_emits_alert(session_factory: sessionmaker[Session]) -> None:
    events: list[ChangeEvent] = []
    clock = FakeClock()
    service = make_service(Script(ok(body(0)), ok(body(3))), session_factory, clock, events)

    service.poll_once()
    clock.advance(10)
    service.poll_once()

    assert [e.kind for e in events] == [EventKind.PRODUCT_RESTOCKED]
    assert events[0].severity is Severity.ALERT
    assert "Kataifi Rolls" in events[0].message
    assert [timed.event for timed in service.state.events] == events
    with session_factory() as db:
        assert len(db.scalars(select(StockSnapshot)).all()) == 7


def test_malformed_response_keeps_last_valid_state(
    session_factory: sessionmaker[Session],
) -> None:
    events: list[ChangeEvent] = []
    clock = FakeClock()
    bad = ok(body(-1))
    service = make_service(Script(ok(body(2)), bad), session_factory, clock, events)

    service.poll_once()
    clock.advance(10)
    decision = service.poll_once()

    state = service.state
    assert state.last_valid is not None
    assert state.last_valid.product(100) is not None
    assert state.last_valid.product(100).stock_remaining == 2  # type: ignore[union-attr]
    assert state.last_valid_at == START
    assert state.consecutive_failures == 1
    assert decision.delay_seconds == 20
    assert [e.kind for e in events] == [EventKind.ENDPOINT_UNAVAILABLE]
    with session_factory() as db:
        cached = snapshot_repository.load_last_state(db, SESSION.slug)
        assert cached is not None
        assert cached[0].product(100).stock_remaining == 2  # type: ignore[union-attr]
        outcomes = [c.outcome for c in db.scalars(select(MonitorCheck).order_by(MonitorCheck.id))]
        assert outcomes == ["ok", "invalid_payload"]


def test_recovery_is_reported_once(session_factory: sessionmaker[Session]) -> None:
    events: list[ChangeEvent] = []
    script = Script(httpx.Response(503), ok(body()), ok(body()))
    service = make_service(script, session_factory, events=events)

    service.poll_once()
    service.poll_once()
    service.poll_once()

    assert [e.kind for e in events] == [
        EventKind.ENDPOINT_UNAVAILABLE,
        EventKind.ENDPOINT_RECOVERED,
    ]
    assert service.state.consecutive_failures == 0


def test_cached_state_prevents_renotifying_after_restart(
    session_factory: sessionmaker[Session],
) -> None:
    make_service(Script(ok(body(3))), session_factory).poll_once()
    events: list[ChangeEvent] = []
    restarted = make_service(Script(ok(body(3))), session_factory, events=events)

    restarted.load_cached()

    assert restarted.state.last_valid is not None
    assert restarted.state.last_valid_is_cached is True
    assert restarted.state.last_valid_at == START
    restarted.poll_once()
    assert events == []
    assert restarted.state.last_valid_is_cached is False


def test_product_names_come_from_catalogue(session_factory: sessionmaker[Session]) -> None:
    service = make_service(Script(), session_factory)

    service.load_cached()

    assert service.state.product_names[100] == "Kataifi Rolls"


def test_schema_drift_is_reported(session_factory: sessionmaker[Session]) -> None:
    events: list[ChangeEvent] = []
    service = make_service(Script(ok(body(queue=5))), session_factory, events=events)

    service.poll_once()

    assert [e.kind for e in events] == [EventKind.SCHEMA_CHANGED]


def test_rate_limit_schedules_retry_after(session_factory: sessionmaker[Session]) -> None:
    clock = FakeClock()
    script = Script(httpx.Response(429, headers={"retry-after": "120"}))
    service = make_service(script, session_factory, clock)

    decision = service.poll_once()

    assert decision.stop is False
    assert decision.delay_seconds == 120
    assert service.state.next_check_at == START + timedelta(seconds=120)


def test_not_found_stops_monitoring(session_factory: sessionmaker[Session]) -> None:
    service = make_service(Script(httpx.Response(404)), session_factory)

    decision = service.poll_once()

    assert decision.stop is True
    assert service.state.stop_reason is not None
    assert "not found" in service.state.stop_reason
    assert service.state.next_check_at is None


def test_database_failure_does_not_stop_monitoring(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[ChangeEvent] = []
    service = make_service(
        Script(ok(body(0)), ok(body(1)), ok(body(2))), session_factory, events=events
    )

    def locked(*args: object, **kwargs: object) -> None:
        raise OperationalError("INSERT", {}, Exception("database is locked"))

    monkeypatch.setattr(snapshot_repository, "record_live_state", locked)
    service.poll_once()
    service.poll_once()
    decision = service.poll_once()

    assert decision.stop is False
    assert service.state.last_valid is not None
    database_events = [e for e in events if e.kind is EventKind.DATABASE_ERROR]
    assert len(database_events) == 1
    assert database_events[0].severity is Severity.ALERT
    assert "OperationalError" in database_events[0].message


def test_run_monitor_polls_until_policy_stops(session_factory: sessionmaker[Session]) -> None:
    clock = FakeClock()
    script = Script(ok(body()), ok(body()), httpx.Response(404))
    service = make_service(script, session_factory, clock)
    sleeps: list[float] = []
    updates: list[int] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.advance(seconds)

    reason = run_monitor(service, sleep=sleep, on_update=lambda state: updates.append(1))

    assert "not found" in reason
    assert len(script.requests) == 3
    assert sum(sleeps) == pytest.approx(20)
    assert max(sleeps) <= 1
    assert len(updates) > 3


def test_live_callback_receives_only_validated_snapshots(
    session_factory: sessionmaker[Session],
) -> None:
    received = []
    service = make_service(
        Script(ok(body(2)), httpx.Response(503)),
        session_factory,
        on_live=received.append,
    )

    service.poll_once()
    service.poll_once()

    assert len(received) == 1
    assert received[0].status.product(100).stock_remaining == 2


def test_live_callback_failure_does_not_break_monitoring(
    session_factory: sessionmaker[Session],
) -> None:
    def fail(_result: object) -> None:
        raise RuntimeError("callback failed")

    service = make_service(Script(ok(body())), session_factory, on_live=fail)

    decision = service.poll_once()

    assert decision.stop is False
    assert service.state.last_valid is not None
