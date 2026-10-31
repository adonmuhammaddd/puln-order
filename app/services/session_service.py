"""Resolve which PULN session to monitor.

A generated slug is only a candidate: ``sessions validate`` must confirm the page exists.
"""

import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit

from app.config import Settings
from app.constants import LIVE_ENDPOINT_SUFFIX, SESSION_SLUG_PREFIX

_SLUG_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class SessionConfigError(ValueError):
    """The configured session cannot be turned into URLs."""


@dataclass(frozen=True)
class SessionRef:
    slug: str
    page_url: str
    live_url: str


def slug_for_date(session_date: date) -> str:
    return f"{SESSION_SLUG_PREFIX}{session_date:%Y%m%d}"


def _from_page_url(url: str) -> SessionRef:
    parts = urlsplit(url)
    path = parts.path.rstrip("/").removesuffix(LIVE_ENDPOINT_SUFFIX).rstrip("/")
    segments = [segment for segment in path.split("/") if segment]
    if len(segments) < 2 or not _SLUG_SEGMENT.fullmatch(segments[-1]):
        raise SessionConfigError("PULN_SESSION_URL must point to a session page, e.g. /c/<slug>")
    page_url = f"{parts.scheme}://{parts.netloc}{path}"
    return SessionRef(
        slug=segments[-1], page_url=page_url, live_url=page_url + LIVE_ENDPOINT_SUFFIX
    )


def resolve_session(settings: Settings) -> SessionRef:
    """Pick the session by precedence: explicit URL, then slug, then date."""
    if settings.puln_session_url is not None:
        return _from_page_url(settings.puln_session_url)

    if settings.puln_session_slug is not None:
        slug = settings.puln_session_slug
    elif settings.puln_session_date is not None:
        slug = slug_for_date(settings.puln_session_date)
    else:
        raise SessionConfigError(
            "No session configured: set PULN_SESSION_DATE, PULN_SESSION_SLUG or PULN_SESSION_URL"
        )
    page_url = f"{settings.puln_base_url}/c/{slug}"
    return SessionRef(slug=slug, page_url=page_url, live_url=page_url + LIVE_ENDPOINT_SUFFIX)
