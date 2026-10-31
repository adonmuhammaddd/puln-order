import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.models.puln import LivePayloadError, LiveStatus, parse_live_status, unknown_fields
from app.ui.timefmt import format_local, to_local

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text())
    return data


def encode(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode()


def test_parses_valid_fixture() -> None:
    status = parse_live_status((FIXTURES / "live_sample.json").read_bytes())

    assert status.status == "OPEN"
    assert status.accepting is True
    assert status.opens_at == datetime(2026, 10, 30, 23, 0, tzinfo=UTC)
    assert status.server_now is not None
    assert status.server_now.tzinfo is not None
    assert [p.id for p in status.products] == [102, 97, 99, 98, 100, 101]
    kataifi = status.product(100)
    assert kataifi is not None
    assert (kataifi.stock_remaining, kataifi.stock_allocated) == (0, 60)
    assert status.slot(185) is not None
    assert len(status.slots) == 6


def test_parses_observed_response_with_fewer_slots() -> None:
    status = parse_live_status((FIXTURES / "live_later.json").read_bytes())

    assert [s.id for s in status.slots] == [178, 183]
    assert status.product(999) is None


def test_optional_fields_may_be_missing() -> None:
    status = parse_live_status(b'{"status": "CLOSED", "accepting": false}')

    assert status.opens_at is None
    assert status.closes_at is None
    assert status.server_now is None
    assert status.products == []
    assert status.slots == []


def test_stock_allocated_is_optional() -> None:
    payload = {"status": "OPEN", "accepting": True, "products": [{"id": 1, "stock_remaining": 3}]}

    assert parse_live_status(encode(payload)).products[0].stock_allocated is None


@pytest.mark.parametrize("bad_value", [-1, "5", "five", 2.5, True, None])
def test_rejects_invalid_stock_values(bad_value: object) -> None:
    payload = load_fixture("live_sample.json")
    payload["products"][0]["stock_remaining"] = bad_value

    with pytest.raises(LivePayloadError):
        parse_live_status(encode(payload))


@pytest.mark.parametrize("bad_value", [-3, "31"])
def test_rejects_invalid_slot_capacity(bad_value: object) -> None:
    payload = load_fixture("live_sample.json")
    payload["slots"][0]["remaining"] = bad_value

    with pytest.raises(LivePayloadError):
        parse_live_status(encode(payload))


@pytest.mark.parametrize("missing", ["status", "accepting"])
def test_rejects_missing_required_fields(missing: str) -> None:
    payload = load_fixture("live_sample.json")
    del payload[missing]

    with pytest.raises(LivePayloadError):
        parse_live_status(encode(payload))


def test_rejects_accepting_given_as_string() -> None:
    payload = load_fixture("live_sample.json")
    payload["accepting"] = "true"

    with pytest.raises(LivePayloadError):
        parse_live_status(encode(payload))


def test_rejects_duplicate_product_ids() -> None:
    payload = load_fixture("live_sample.json")
    payload["products"].append({"id": 100, "stock_remaining": 5, "stock_allocated": 60})

    with pytest.raises(LivePayloadError, match="duplicate"):
        parse_live_status(encode(payload))


@pytest.mark.parametrize("content", [b"", b"<html>blocked</html>", b"[1, 2]", b'{"status": '])
def test_rejects_non_object_or_invalid_json(content: bytes) -> None:
    with pytest.raises(LivePayloadError):
        parse_live_status(content)


def test_error_message_does_not_echo_raw_payload() -> None:
    payload = load_fixture("live_sample.json")
    payload["status"] = {"secret": "should-not-appear"}

    with pytest.raises(LivePayloadError) as exc_info:
        parse_live_status(encode(payload))

    assert "should-not-appear" not in str(exc_info.value)


def test_tolerates_and_reports_unexpected_fields() -> None:
    payload = load_fixture("live_sample.json")
    payload["queue_position"] = 3
    payload["products"][0]["name"] = "Example"
    payload["slots"][0]["capacity"] = 50

    status = parse_live_status(encode(payload))

    assert unknown_fields(status) == {"queue_position", "products[].name", "slots[].capacity"}


def test_known_payload_has_no_unknown_fields() -> None:
    status = parse_live_status((FIXTURES / "live_sample.json").read_bytes())

    assert unknown_fields(status) == set()


def test_round_trips_through_json_without_unknown_fields() -> None:
    payload = load_fixture("live_sample.json")
    payload["queue_position"] = 3
    status = parse_live_status(encode(payload))

    restored = LiveStatus.model_validate_json(status.to_json())

    assert restored.products == status.products
    assert restored.server_now == status.server_now
    assert unknown_fields(restored) == set()


def test_converts_utc_to_jakarta_for_display() -> None:
    utc_time = datetime(2026, 10, 30, 23, 0, tzinfo=UTC)

    local = to_local(utc_time, "Asia/Jakarta")

    assert (local.day, local.hour) == (31, 6)
    assert format_local(utc_time, "Asia/Jakarta") == "2026-10-31 06:00:00 WIB"


def test_format_local_handles_missing_value() -> None:
    assert format_local(None, "Asia/Jakarta") == "-"
