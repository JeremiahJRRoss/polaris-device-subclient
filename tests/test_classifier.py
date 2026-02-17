"""Tests for the classifier module."""

import orjson
import pytest

from polaris_device_subclient.classifier import classify, MAX_RAW_PAYLOAD_BYTES
from polaris_device_subclient.models import MalformedEvent


def _make_next_message(devices: dict) -> str:
    """Build a well-formed ``next`` message string."""
    return orjson.dumps({
        "id": "1",
        "type": "next",
        "payload": {"data": {"devices": devices}},
    }).decode()


VALID_DEVICES = {
    "id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
    "label": "Fleet-Truck-042",
    "tags": [
        {"key": "fleet", "value": "west-coast"},
        {"key": "type", "value": "delivery"},
    ],
    "lastPosition": {
        "position": {
            "llaDec": {"lat": 37.7749295, "lon": -122.4194155, "alt": 10.5},
        },
        "timestamp": "2025-02-15T18:32:01.123Z",
    },
    "services": {
        "rtk": {"enabled": True, "connectionStatus": "CONNECTED"},
    },
}


def test_valid_next_message() -> None:
    """A well-formed ``next`` message returns the devices dict."""
    raw = _make_next_message(VALID_DEVICES)
    result = classify(raw, instance_id="test")
    assert isinstance(result, dict)
    assert result["id"] == "d290f1ee-6c54-4b01-90e6-d701748f0851"
    assert result["label"] == "Fleet-Truck-042"


def test_invalid_json() -> None:
    """Broken JSON yields a MalformedEvent with ``parse_error``."""
    result = classify("{not valid json!!!", instance_id="test")
    assert isinstance(result, MalformedEvent)
    assert result.error["code"] == "parse_error"


def test_wrong_message_type() -> None:
    """A non-``next`` message (e.g. ``connection_ack``) returns None."""
    raw = orjson.dumps({"type": "connection_ack"}).decode()
    result = classify(raw)
    assert result is None


def test_missing_payload_path() -> None:
    """``next`` with no ``payload.data.devices`` → schema_mismatch."""
    raw = orjson.dumps({
        "id": "1",
        "type": "next",
        "payload": {"data": {}},
    }).decode()
    result = classify(raw, instance_id="test")
    assert isinstance(result, MalformedEvent)
    assert result.error["code"] == "schema_mismatch"


def test_missing_device_id() -> None:
    """Valid structure but missing ``id`` → missing_fields."""
    raw = _make_next_message({"label": "no-id-device"})
    result = classify(raw, instance_id="test")
    assert isinstance(result, MalformedEvent)
    assert result.error["code"] == "missing_fields"


def test_raw_payload_truncation() -> None:
    """Payloads exceeding 4096 bytes are truncated in malformed records."""
    # Build an oversized payload that fails classification
    big_str = "x" * (MAX_RAW_PAYLOAD_BYTES + 1000)
    raw = f'{{"not": "{big_str}"'  # intentionally invalid JSON (no closing brace)
    result = classify(raw, instance_id="test")
    assert isinstance(result, MalformedEvent)
    assert result.error["raw_payload_truncated"] is True
    assert len(result.error["raw_payload"]) <= MAX_RAW_PAYLOAD_BYTES
