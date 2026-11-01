import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.clients.puln_client import (
    MAX_BODY_BYTES,
    Forbidden,
    InvalidPayload,
    LiveOk,
    NetworkError,
    NotFound,
    PulnClient,
    RateLimited,
    ServerError,
    UnexpectedStatus,
    create_http_client,
)
from app.constants import USER_AGENT

LIVE_URL = "https://puln.seatin.id/c/online-order-20261031/live.json"
PAGE_URL = "https://puln.seatin.id/c/online-order-20261031"
SAMPLE = (Path(__file__).parent / "fixtures" / "live_sample.json").read_bytes()
NOW = datetime(2026, 10, 31, 1, 0, tzinfo=UTC)
JSON = {"content-type": "application/json; charset=utf-8"}

Handler = Callable[[httpx.Request], httpx.Response]


class FakeTimer:
    def __init__(self, step: float) -> None:
        self._values: Iterator[float] = iter(x * step for x in range(1000))

    def __call__(self) -> float:
        return next(self._values)


def make_client(handler: Handler, timer_step: float = 0.25) -> PulnClient:
    http = create_http_client(timeout=5, transport=httpx.MockTransport(handler))
    return PulnClient(http, timer=FakeTimer(timer_step), clock=lambda: NOW)


def respond(status: int, content: bytes = b"", headers: dict[str, str] | None = None) -> Handler:
    return lambda request: httpx.Response(status, content=content, headers=headers)


def raise_error(error: Exception) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    return handler


def test_successful_fetch_returns_parsed_status() -> None:
    result = make_client(respond(200, SAMPLE, JSON)).fetch_live(LIVE_URL)

    assert isinstance(result, LiveOk)
    assert result.http_status == 200
    assert result.status.product(100) is not None
    assert result.unknown_fields == frozenset()


def test_measures_request_duration() -> None:
    result = make_client(respond(200, SAMPLE, JSON), timer_step=0.333).fetch_live(LIVE_URL)

    assert result.duration_ms == 333


def test_sends_honest_headers_without_cookies() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=SAMPLE, headers={**JSON, "set-cookie": "__cf_bm=abc"})

    client = make_client(handler)
    client.fetch_live(LIVE_URL)
    client.fetch_live(LIVE_URL)

    assert seen[0].headers["user-agent"] == USER_AGENT
    assert "json" in seen[0].headers["accept"]
    assert "cookie" not in seen[1].headers


def test_reports_unknown_fields() -> None:
    body = SAMPLE.replace(b'"status"', b'"queue": 1, "status"', 1)

    result = make_client(respond(200, body, JSON)).fetch_live(LIVE_URL)

    assert isinstance(result, LiveOk)
    assert result.unknown_fields == frozenset({"queue"})


def test_html_body_is_invalid_payload() -> None:
    result = make_client(
        respond(200, b"<html>Just a moment...</html>", {"content-type": "text/html"})
    ).fetch_live(LIVE_URL)

    assert isinstance(result, InvalidPayload)
    assert "HTML" in result.reason


def test_malformed_json_is_invalid_payload() -> None:
    result = make_client(respond(200, b'{"status": ', JSON)).fetch_live(LIVE_URL)

    assert isinstance(result, InvalidPayload)
    assert result.http_status == 200


def test_schema_violation_is_invalid_payload() -> None:
    body = SAMPLE.replace(b'"stock_remaining": 39', b'"stock_remaining": -1')

    result = make_client(respond(200, body, JSON)).fetch_live(LIVE_URL)

    assert isinstance(result, InvalidPayload)
    assert "stock_remaining" in result.reason


def test_oversized_body_is_invalid_payload() -> None:
    result = make_client(respond(200, b" " * (MAX_BODY_BYTES + 1), JSON)).fetch_live(LIVE_URL)

    assert isinstance(result, InvalidPayload)
    assert "too large" in result.reason


def test_404_means_session_not_found() -> None:
    assert isinstance(make_client(respond(404)).fetch_live(LIVE_URL), NotFound)


def test_403_is_forbidden() -> None:
    result = make_client(respond(403)).fetch_live(LIVE_URL)

    assert isinstance(result, Forbidden)
    assert result.challenge is False


def test_403_with_cloudflare_challenge_is_flagged() -> None:
    result = make_client(respond(403, b"<html/>", {"cf-mitigated": "challenge"})).fetch_live(
        LIVE_URL
    )

    assert isinstance(result, Forbidden)
    assert result.challenge is True


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("30", 30.0),
        ("Sat, 31 Oct 2026 01:01:30 GMT", 90.0),
        ("Sat, 31 Oct 2026 00:59:00 GMT", 0.0),
        ("soon", None),
        ("-5", None),
    ],
)
def test_429_parses_retry_after(header: str, expected: float | None) -> None:
    result = make_client(respond(429, headers={"retry-after": header})).fetch_live(LIVE_URL)

    assert isinstance(result, RateLimited)
    assert result.retry_after_seconds == expected


def test_429_without_retry_after() -> None:
    result = make_client(respond(429)).fetch_live(LIVE_URL)

    assert isinstance(result, RateLimited)
    assert result.retry_after_seconds is None


@pytest.mark.parametrize("status", [500, 502, 503])
def test_5xx_is_server_error(status: int) -> None:
    result = make_client(respond(status)).fetch_live(LIVE_URL)

    assert isinstance(result, ServerError)
    assert result.http_status == status


def test_redirect_is_not_followed() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://puln.seatin.id/"})

    result = make_client(handler).fetch_live(LIVE_URL)

    assert isinstance(result, UnexpectedStatus)
    assert result.http_status == 302
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.ConnectTimeout("slow"), "timeout"),
        (httpx.ConnectError("dns failure"), "connect"),
        (httpx.RemoteProtocolError("broken"), "other"),
    ],
)
def test_network_failures_are_classified(error: Exception, kind: str) -> None:
    result = make_client(raise_error(error)).fetch_live(LIVE_URL)

    assert isinstance(result, NetworkError)
    assert result.kind == kind
    assert result.http_status is None


def test_logs_status_and_duration_without_secrets(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.clients.puln_client")
    handler = respond(200, SAMPLE, {**JSON, "set-cookie": "__cf_bm=secretvalue"})

    make_client(handler).fetch_live(LIVE_URL)

    assert "HTTP 200" in caplog.text
    assert "250 ms" in caplog.text
    assert "secretvalue" not in caplog.text


def test_check_page_reports_reachable_session() -> None:
    page = make_client(respond(200, b"<html></html>", {"content-type": "text/html"})).check_page(
        PAGE_URL
    )

    assert page.reachable is True
    assert page.http_status == 200


def test_check_page_reports_missing_session() -> None:
    page = make_client(respond(404)).check_page(PAGE_URL)

    assert page.reachable is False
    assert page.http_status == 404


def test_check_page_reports_network_failure() -> None:
    page = make_client(raise_error(httpx.ConnectError("offline"))).check_page(PAGE_URL)

    assert page.reachable is False
    assert page.http_status is None
    assert "connect" in page.detail



def test_fetch_catalogue_parses_product_cards() -> None:
    html = (
        b'<li class="product" data-product="105" '
        b'data-name="Kataifi Crunch (Personal)" data-max-per="">'
        b'<p data-price="70000.00"></p></li>'
    )
    response = respond(200, html, {"content-type": "text/html"})
    result = make_client(response).fetch_catalogue(PAGE_URL)
    assert len(result) == 1
    assert result[0].puln_product_id == 105
    assert result[0].variant_key == "kataifi-rolls"
    assert result[0].max_quantity is None


def test_fetch_catalogue_rejects_non_html_response() -> None:
    with pytest.raises(RuntimeError, match="not HTML"):
        make_client(respond(200, SAMPLE, JSON)).fetch_catalogue(PAGE_URL)


def test_fetch_catalogue_rejects_empty_product_list() -> None:
    with pytest.raises(RuntimeError, match="no product cards"):
        make_client(
            respond(200, b"<html></html>", {"content-type": "text/html"})
        ).fetch_catalogue(PAGE_URL)
