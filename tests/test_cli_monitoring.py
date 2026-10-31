import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app.main import main

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = (FIXTURES / "live_sample.json").read_bytes()
JSON = {"content-type": "application/json"}

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
def session_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PULN_SESSION_DATE", "2026-10-31")


def routes(page: httpx.Response, live: httpx.Response) -> tuple[Handler, list[str]]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return live if request.url.path.endswith("/live.json") else page

    return handler, seen


def test_status_fetches_once_and_prints_dashboard(capsys: pytest.CaptureFixture[str]) -> None:
    handler, seen = routes(httpx.Response(200), httpx.Response(200, content=SAMPLE, headers=JSON))

    exit_code = main(["status"], transport=httpx.MockTransport(handler))

    out = capsys.readouterr().out
    assert exit_code == 0
    assert seen == ["GET /c/online-order-20261031/live.json"]
    assert "Kataifi Rolls" in out
    assert "LIVE" in out


def test_status_reports_failure_with_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    handler, _ = routes(httpx.Response(200), httpx.Response(503))

    exit_code = main(["status"], transport=httpx.MockTransport(handler))

    assert exit_code == 1
    assert "HTTP 503" in capsys.readouterr().out


def test_status_shows_cached_data_after_failure(capsys: pytest.CaptureFixture[str]) -> None:
    good, _ = routes(httpx.Response(200), httpx.Response(200, content=SAMPLE, headers=JSON))
    bad, _ = routes(httpx.Response(200), httpx.Response(503))
    main(["status"], transport=httpx.MockTransport(good))
    capsys.readouterr()

    main(["status"], transport=httpx.MockTransport(bad))

    out = capsys.readouterr().out
    assert "LAST KNOWN" in out
    assert "Kataifi Rolls" in out


def test_missing_session_configuration_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("PULN_SESSION_DATE")

    exit_code = main(["status"], transport=httpx.MockTransport(lambda r: httpx.Response(500)))

    assert exit_code == 2
    assert "PULN_SESSION_DATE" in capsys.readouterr().err


def test_sessions_validate_checks_page_and_endpoint(capsys: pytest.CaptureFixture[str]) -> None:
    handler, seen = routes(
        httpx.Response(200, content=b"<html></html>", headers={"content-type": "text/html"}),
        httpx.Response(200, content=SAMPLE, headers=JSON),
    )

    exit_code = main(["sessions", "validate"], transport=httpx.MockTransport(handler))

    out = capsys.readouterr().out
    assert exit_code == 0
    assert seen == ["GET /c/online-order-20261031", "GET /c/online-order-20261031/live.json"]
    assert "https://puln.seatin.id/c/online-order-20261031" in out
    assert "valid" in out.lower()
    assert "6 products" in out
    assert "2026-10-31 06:00:00 WIB" in out


def test_sessions_validate_fails_for_missing_session(capsys: pytest.CaptureFixture[str]) -> None:
    handler, _ = routes(httpx.Response(404), httpx.Response(404))

    exit_code = main(["sessions", "validate"], transport=httpx.MockTransport(handler))

    assert exit_code == 1
    assert "404" in capsys.readouterr().out


def test_sessions_validate_reports_invalid_payload(capsys: pytest.CaptureFixture[str]) -> None:
    payload = json.loads(SAMPLE)
    payload["products"][0]["stock_remaining"] = -4
    handler, _ = routes(
        httpx.Response(200), httpx.Response(200, content=json.dumps(payload), headers=JSON)
    )

    exit_code = main(["sessions", "validate"], transport=httpx.MockTransport(handler))

    assert exit_code == 1
    assert "stock_remaining" in capsys.readouterr().out


def test_monitor_stops_when_session_is_missing(capsys: pytest.CaptureFixture[str]) -> None:
    handler, seen = routes(httpx.Response(200), httpx.Response(404))

    exit_code = main(["monitor", "--no-bell"], transport=httpx.MockTransport(handler))

    out = capsys.readouterr().out
    assert exit_code == 1
    assert len(seen) == 1
    assert "not found" in out


def test_monitor_stops_cleanly_when_session_closed(capsys: pytest.CaptureFixture[str]) -> None:
    payload = json.loads(SAMPLE)
    payload.update(status="CLOSED", accepting=False, server_now="2026-10-31T09:00:00Z")
    handler, seen = routes(
        httpx.Response(200), httpx.Response(200, content=json.dumps(payload), headers=JSON)
    )

    exit_code = main(["monitor"], transport=httpx.MockTransport(handler))

    assert exit_code == 0
    assert len(seen) == 1
    assert "no longer accepting" in capsys.readouterr().out


def test_products_name_and_list(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["products", "name", "102", "Example Product"]) == 0
    capsys.readouterr()

    assert main(["products", "list"]) == 0

    out = capsys.readouterr().out
    assert "Example Product" in out
    assert "Kataifi Rolls" in out


def test_products_name_rejects_blank_name(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["products", "name", "102", "  "]) == 2
    assert "name" in capsys.readouterr().err
