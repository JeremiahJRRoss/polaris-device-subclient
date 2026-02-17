"""Tests for the filter module."""

import pytest

from polaris_device_subclient.config import FilterConfig
from polaris_device_subclient.filter import EventFilter


def _device(
    device_id: str = "dev-001",
    connection_status: str | None = "CONNECTED",
) -> dict:
    """Build a minimal device dict for filter tests."""
    d: dict = {"id": device_id}
    if connection_status is not None:
        d["services"] = {"rtk": {"connectionStatus": connection_status}}
    return d


def test_drop_state() -> None:
    """Device with connectionStatus in drop_states is filtered out."""
    cfg = FilterConfig(drop_states=["undefined", "error"])
    f = EventFilter(cfg)
    assert f.apply(_device(connection_status="undefined")) is None


def test_pass_state() -> None:
    """Device with connectionStatus NOT in drop_states passes."""
    cfg = FilterConfig(drop_states=["undefined", "error"])
    f = EventFilter(cfg)
    result = f.apply(_device(connection_status="CONNECTED"))
    assert result is not None
    assert result["id"] == "dev-001"


def test_drop_device_id() -> None:
    """Device in drop_device_ids is filtered out."""
    cfg = FilterConfig(drop_device_ids=["noisy-99"])
    f = EventFilter(cfg)
    assert f.apply(_device(device_id="noisy-99")) is None


def test_keep_device_ids_match() -> None:
    """Device in keep_device_ids passes."""
    cfg = FilterConfig(keep_device_ids=["dev-001", "dev-002"])
    f = EventFilter(cfg)
    result = f.apply(_device(device_id="dev-001"))
    assert result is not None


def test_keep_device_ids_no_match() -> None:
    """Device NOT in keep_device_ids is filtered."""
    cfg = FilterConfig(keep_device_ids=["dev-001", "dev-002"])
    f = EventFilter(cfg)
    assert f.apply(_device(device_id="dev-999")) is None


def test_empty_keep_allows_all() -> None:
    """When keep_device_ids is empty, all devices pass."""
    cfg = FilterConfig(keep_device_ids=[])
    f = EventFilter(cfg)
    result = f.apply(_device(device_id="any-device"))
    assert result is not None


def test_no_connection_status() -> None:
    """Missing connectionStatus field → passes (don't filter on missing data)."""
    cfg = FilterConfig(drop_states=["undefined", "error"])
    f = EventFilter(cfg)
    result = f.apply(_device(connection_status=None))
    assert result is not None
