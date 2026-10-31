import json

import httpx
import pytest

from app.main import main
from tests.test_cli_monitoring import JSON, SAMPLE


@pytest.fixture(autouse=True)
def two_checks(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Two status refreshes: Kataifi goes from 0 to 4 between them."""
    monkeypatch.setenv("PULN_SESSION_DATE", "2026-10-31")
    restocked = json.loads(SAMPLE)
    for product in restocked["products"]:
        if product["id"] == 100:
            product["stock_remaining"] = 4
    for body in (SAMPLE, json.dumps(restocked).encode()):
        transport = httpx.MockTransport(
            lambda r, body=body: httpx.Response(200, content=body, headers=JSON)
        )
        main(["status"], transport=transport)
    capsys.readouterr()


def test_report_summarises_session(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["report"]) == 0

    out = capsys.readouterr().out
    assert "online-order-20261031" in out
    assert "Checks: 2" in out
    assert "ok 2" in out
    assert "Kataifi Rolls" in out
    assert "Restocked at" in out
    assert "WIB" in out


def test_history_stock_lists_changes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["history", "stock", "100"]) == 0

    lines = [line for line in capsys.readouterr().out.splitlines() if "WIB" in line]
    assert len(lines) == 2
    assert lines[0].rstrip().endswith("0")
    assert lines[1].rstrip().endswith("4")


def test_history_slot(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["history", "slot", "185"]) == 0

    lines = [line for line in capsys.readouterr().out.splitlines() if "WIB" in line]
    assert len(lines) == 1


def test_history_without_data(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["history", "stock", "555"]) == 0
    assert "No recorded changes" in capsys.readouterr().out
