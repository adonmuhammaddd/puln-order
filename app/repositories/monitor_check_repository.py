"""One row per live-endpoint request: outcome and timing only, never response content."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.puln_client import (
    FetchResult,
    Forbidden,
    InvalidPayload,
    NetworkError,
    RateLimited,
)
from app.database.tables import MonitorCheck

_DETAIL_LENGTH = 255


def _detail(result: FetchResult) -> str | None:
    match result:
        case NetworkError(kind=kind):
            return kind
        case InvalidPayload(reason=reason):
            return reason[:_DETAIL_LENGTH]
        case RateLimited(retry_after_seconds=seconds) if seconds is not None:
            return f"retry-after {seconds:.0f}s"
        case Forbidden(challenge=True):
            return "challenge"
        case _:
            return None


def record_check(
    session: Session, slug: str, result: FetchResult, started_at: datetime
) -> MonitorCheck:
    check = MonitorCheck(
        session_slug=slug,
        started_at=started_at,
        duration_ms=result.duration_ms,
        http_status=result.http_status,
        outcome=result.outcome,
        detail=_detail(result),
    )
    session.add(check)
    session.flush()
    return check


def latest_check(session: Session, slug: str) -> MonitorCheck | None:
    return session.scalars(
        select(MonitorCheck)
        .where(MonitorCheck.session_slug == slug)
        .order_by(MonitorCheck.started_at.desc(), MonitorCheck.id.desc())
        .limit(1)
    ).one_or_none()


def last_successful_check(session: Session, slug: str) -> MonitorCheck | None:
    return session.scalars(
        select(MonitorCheck)
        .where(MonitorCheck.session_slug == slug, MonitorCheck.outcome == "ok")
        .order_by(MonitorCheck.started_at.desc(), MonitorCheck.id.desc())
        .limit(1)
    ).one_or_none()
