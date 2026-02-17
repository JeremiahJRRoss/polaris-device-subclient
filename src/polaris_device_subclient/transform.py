"""Transform Polaris device events into NDJSON ``state_change`` records.

Maintains an in-memory ``device_id → last_connectionStatus`` dict so that
each record carries the *previous* state.  The dict is **not** persisted —
on restart every device begins with ``previous_state: null``.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional

import orjson

from polaris_device_subclient.models import MalformedEvent, StateChangeEvent


class Transformer:
    """Stateful transform: Polaris device dict → serialized NDJSON bytes."""

    def __init__(self, instance_id: str = "", subscription_id: Optional[str] = None) -> None:
        self._instance_id = instance_id
        self._subscription_id = subscription_id
        self._state: dict[str, str] = {}  # device_id → last connectionStatus

    def transform(self, devices: dict) -> bytes:
        """Convert a validated *devices* dict into a newline-terminated NDJSON line.

        Parameters
        ----------
        devices:
            The device object as returned by the classifier.

        Returns
        -------
        bytes
            ``orjson``-serialized NDJSON line (newline-terminated).
        """
        device_id: str = devices.get("id", "")
        current_state = _safe_get(devices, "services", "rtk", "connectionStatus")
        previous_state = self._state.get(device_id)

        if current_state is not None:
            self._state[device_id] = current_state

        # Extract position fields
        position = _safe_get(devices, "lastPosition", "position", "llaDec") or {}
        timestamp = _safe_get(devices, "lastPosition", "timestamp")

        event = StateChangeEvent(
            event_type="state_change",
            timestamp=timestamp,
            received_at=datetime.now(timezone.utc).isoformat(),
            device_id=device_id,
            device_label=devices.get("label"),
            previous_state=previous_state,
            current_state=current_state,
            latitude=position.get("lat"),
            longitude=position.get("lon"),
            altitude_m=position.get("alt"),
            rtk_enabled=_safe_get(devices, "services", "rtk", "enabled"),
            tags=devices.get("tags"),
            source={
                "instance_id": self._instance_id,
                "subscription_id": self._subscription_id,
            },
        )

        return orjson.dumps(asdict(event), option=orjson.OPT_APPEND_NEWLINE)

    def transform_malformed(self, malformed: MalformedEvent) -> bytes:
        """Serialize a :class:`MalformedEvent` to NDJSON bytes."""
        return orjson.dumps(asdict(malformed), option=orjson.OPT_APPEND_NEWLINE)


def _safe_get(obj: dict, *keys: str):
    """Walk nested dicts, returning ``None`` on any missing key."""
    current = obj
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current
