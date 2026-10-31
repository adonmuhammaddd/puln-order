"""Read-only client for the public PULN session page and live.json endpoint.

It never posts, never follows redirects, never keeps cookies between requests and never
retries on its own: retry timing belongs to the poll policy.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import ClassVar, Literal

import httpx

from app.clock import utc_now
from app.constants import USER_AGENT
from app.models.puln import LivePayloadError, LiveStatus, parse_live_status, unknown_fields

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 1_000_000


@dataclass(frozen=True)
class LiveOk:
    outcome: ClassVar[str] = "ok"
    http_status: int
    duration_ms: int
    status: LiveStatus
    unknown_fields: frozenset[str]


@dataclass(frozen=True)
class RateLimited:
    outcome: ClassVar[str] = "rate_limited"
    http_status: int
    duration_ms: int
    retry_after_seconds: float | None


@dataclass(frozen=True)
class NotFound:
    outcome: ClassVar[str] = "not_found"
    http_status: int
    duration_ms: int


@dataclass(frozen=True)
class Forbidden:
    outcome: ClassVar[str] = "forbidden"
    http_status: int
    duration_ms: int
    challenge: bool


@dataclass(frozen=True)
class ServerError:
    outcome: ClassVar[str] = "server_error"
    http_status: int
    duration_ms: int


@dataclass(frozen=True)
class UnexpectedStatus:
    outcome: ClassVar[str] = "unexpected_status"
    http_status: int
    duration_ms: int


@dataclass(frozen=True)
class InvalidPayload:
    outcome: ClassVar[str] = "invalid_payload"
    http_status: int
    duration_ms: int
    reason: str


@dataclass(frozen=True)
class NetworkError:
    outcome: ClassVar[str] = "network_error"
    duration_ms: int
    kind: Literal["timeout", "connect", "other"]
    message: str
    http_status: None = None


FetchResult = (
    LiveOk
    | RateLimited
    | NotFound
    | Forbidden
    | ServerError
    | UnexpectedStatus
    | InvalidPayload
    | NetworkError
)


@dataclass(frozen=True)
class PageCheck:
    reachable: bool
    http_status: int | None
    detail: str


def create_http_client(
    timeout: float, transport: httpx.BaseTransport | None = None
) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        transport=transport,
    )


def parse_retry_after(value: str | None, now: datetime) -> float | None:
    """Seconds to wait from a Retry-After header (delta-seconds or HTTP-date)."""
    if value is None:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if retry_at.tzinfo is None:
        return None
    return max(0.0, (retry_at - now).total_seconds())


def _classify_network_error(error: httpx.HTTPError) -> Literal["timeout", "connect", "other"]:
    if isinstance(error, httpx.TimeoutException):
        return "timeout"
    if isinstance(error, httpx.ConnectError):
        return "connect"
    return "other"


class PulnClient:
    def __init__(
        self,
        http: httpx.Client,
        *,
        timer: Callable[[], float] = time.perf_counter,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._http = http
        self._timer = timer
        self._clock = clock

    def _get(self, url: str, accept: str) -> tuple[httpx.Response | httpx.HTTPError, int]:
        started = self._timer()
        try:
            response: httpx.Response | httpx.HTTPError = self._http.get(
                url, headers={"Accept": accept}
            )
        except httpx.HTTPError as error:
            response = error
        finally:
            # Cookies are only meaningful to the site's own browser flow; never carry them over.
            self._http.cookies.clear()
        return response, round((self._timer() - started) * 1000)

    def fetch_live(self, url: str) -> FetchResult:
        response, duration_ms = self._get(url, "application/json")
        result = self._interpret(response, duration_ms)
        status_text = f"HTTP {result.http_status}" if result.http_status else "no response"
        logger.info("GET %s -> %s (%s) in %d ms", url, status_text, result.outcome, duration_ms)
        return result

    def _interpret(
        self, response: httpx.Response | httpx.HTTPError, duration_ms: int
    ) -> FetchResult:
        if isinstance(response, httpx.HTTPError):
            return NetworkError(
                duration_ms, _classify_network_error(response), type(response).__name__
            )

        code = response.status_code
        if code == 200:
            return self._parse_body(response, duration_ms)
        if code == 404:
            return NotFound(code, duration_ms)
        if code == 403:
            challenge = response.headers.get("cf-mitigated", "").lower() == "challenge"
            return Forbidden(code, duration_ms, challenge)
        if code == 429:
            retry_after = parse_retry_after(response.headers.get("retry-after"), self._clock())
            return RateLimited(code, duration_ms, retry_after)
        if code >= 500:
            return ServerError(code, duration_ms)
        return UnexpectedStatus(code, duration_ms)

    @staticmethod
    def _parse_body(response: httpx.Response, duration_ms: int) -> FetchResult:
        content_type = response.headers.get("content-type", "").lower()
        if "html" in content_type:
            return InvalidPayload(
                200, duration_ms, "received HTML instead of JSON (possibly a challenge page)"
            )
        if len(response.content) > MAX_BODY_BYTES:
            return InvalidPayload(200, duration_ms, "response body too large")
        try:
            status = parse_live_status(response.content)
        except LivePayloadError as error:
            return InvalidPayload(200, duration_ms, str(error))
        return LiveOk(200, duration_ms, status, frozenset(unknown_fields(status)))

    def check_page(self, url: str) -> PageCheck:
        """Confirm the official session page exists. The page body is not parsed or stored."""
        response, duration_ms = self._get(url, "text/html")
        if isinstance(response, httpx.HTTPError):
            kind = _classify_network_error(response)
            logger.info("GET %s -> no response (%s) in %d ms", url, kind, duration_ms)
            return PageCheck(False, None, f"{kind} error")
        logger.info("GET %s -> HTTP %d in %d ms", url, response.status_code, duration_ms)
        if response.status_code == 200:
            return PageCheck(True, 200, "session page found")
        return PageCheck(False, response.status_code, f"HTTP {response.status_code}")
