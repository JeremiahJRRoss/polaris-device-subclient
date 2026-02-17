"""Tests for the transform module."""

import orjson
import pytest

from polaris_device_subclient.transform import Transformer


FULL_DEVICE = {
    "id": "dev-abc-123",
    "label": "Fleet-Truck-042",
    "tags": [{"key": "fleet", "value": "west-coast"}],
    "lastPosition": {
        "position": {
            "llaDec": {"lat": 37.7749, "lon": -122.4194, "alt": 10.5},
        },
        "timestamp": "2025-02-15T18:32:01.123Z",
    },
    "services": {
        "rtk": {"enabled": True, "connectionStatus": "CONNECTED"},
    },
}


def test_full_event_mapping() -> None:
    """All fields present → correct NDJSON output with expected values."""
    xform = Transformer(instance_id="test-01", subscription_id="sub-1")
    data = xform.transform(FULL_DEVICE)
    record = orjson.loads(data)

    assert record["event_type"] == "state_change"
    assert record["device_id"] == "dev-abc-123"
    assert record["device_label"] == "Fleet-Truck-042"
    assert record["current_state"] == "CONNECTED"
    assert record["latitude"] == 37.7749
    assert record["longitude"] == -122.4194
    assert record["altitude_m"] == 10.5
    assert record["rtk_enabled"] is True
    assert record["timestamp"] == "2025-02-15T18:32:01.123Z"
    assert record["source"]["instance_id"] == "test-01"
    assert record["source"]["subscription_id"] == "sub-1"
    assert record["tags"] == [{"key": "fleet", "value": "west-coast"}]


def test_first_event_null_previous() -> None:
    """First time seeing a device → previous_state is null."""
    xform = Transformer(instance_id="test-01")
    data = xform.transform(FULL_DEVICE)
    record = orjson.loads(data)
    assert record["previous_state"] is None


def test_state_tracking() -> None:
    """Second event for the same device → previous_state equals first state."""
    xform = Transformer(instance_id="test-01")

    # First event
    xform.transform(FULL_DEVICE)

    # Second event with different state
    device2 = {
        "id": "dev-abc-123",
        "services": {"rtk": {"connectionStatus": "DISCONNECTED"}},
    }
    data = xform.transform(device2)
    record = orjson.loads(data)

    assert record["previous_state"] == "CONNECTED"
    assert record["current_state"] == "DISCONNECTED"


def test_partial_payload() -> None:
    """Only ``id`` and ``connectionStatus`` → other fields are null."""
    xform = Transformer(instance_id="test-01")
    device = {
        "id": "dev-minimal",
        "services": {"rtk": {"connectionStatus": "CONNECTED"}},
    }
    data = xform.transform(device)
    record = orjson.loads(data)

    assert record["device_id"] == "dev-minimal"
    assert record["current_state"] == "CONNECTED"
    assert record["device_label"] is None
    assert record["latitude"] is None
    assert record["longitude"] is None
    assert record["altitude_m"] is None
    assert record["tags"] is None


def test_output_is_newline_terminated_bytes() -> None:
    """Result ends with ``\\n`` and is bytes type."""
    xform = Transformer(instance_id="test-01")
    data = xform.transform(FULL_DEVICE)
    assert isinstance(data, bytes)
    assert data.endswith(b"\n")
