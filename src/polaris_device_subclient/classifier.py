"""Classify raw WebSocket messages into valid device events or malformed records.

Classification pipeline::

    raw string
      │
      ├─ JSON parse failure  → MalformedEvent(code="parse_error")
      ├─ type ≠ "next"       → None  (protocol message, skip)
      ├─ missing path        → MalformedEvent(code="schema_mismatch")
      ├─ missing device id   → MalformedEvent(code="missing_fields")
      └─ valid               → dict  (the ``devices`` object)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Union

import orjson

from polaris_device_subclient.models import MalformedEvent

# Maximum bytes of raw payload preserved in malformed events.
MAX_RAW_PAYLOAD_BYTES = 4096


def classify(
    raw: str | bytes,
    instance_id: str = "",
    subscription_id: Optional[str] = None,
) -> Union[dict, MalformedEvent, None]:
    """Classify a single raw WebSocket message.

    Parameters
    ----------
    raw:
        The raw message string (or bytes) received from the WebSocket.
    instance_id:
        Value for ``source.instance_id`` in malformed records.
    subscription_id:
        Value for ``source.subscription_id`` in malformed records.

    Returns
    -------
    dict
        The validated ``devices`` object when the message is a well-formed
        ``next`` event containing a device payload with an ``id``.
    MalformedEvent
        When the message cannot be parsed or fails structural checks.
    None
        When the message is a valid protocol message that is not a data
        event (e.g. ``connection_ack``, ``ping``, ``complete``).
    """
    now = datetime.now(timezone.utc).isoformat()
    source = {"instance_id": instance_id, "subscription_id": subscription_id}

    # Step 1: parse JSON
    try:
        if isinstance(raw, str):
            msg = orjson.loads(raw.encode("utf-8") if isinstance(raw, str) else raw)
        else:
            msg = orjson.loads(raw)
    except Exception as exc:
        return _malformed(
            code="parse_error",
            message=str(exc),
            raw=raw,
            now=now,
            source=source,
        )

    # Step 2: check message type
    msg_type = msg.get("type")
    if msg_type != "next":
        return None  # protocol-level message — not a data event

    # Step 3: extract devices payload
    try:
        devices = msg["payload"]["data"]["devices"]
    except (KeyError, TypeError):
        return _malformed(
            code="schema_mismatch",
            message="Missing path: payload.data.devices",
            raw=raw,
            now=now,
            source=source,
        )

    # Step 4: require device id
    if not isinstance(devices, dict) or "id" not in devices:
        return _malformed(
            code="missing_fields",
            message="Device object missing required field: id",
            raw=raw,
            now=now,
            source=source,
        )

    return devices


# ── helpers ─────────────────────────────────────────────────────────


def _malformed(
    code: str,
    message: str,
    raw: str | bytes,
    now: str,
    source: dict,
) -> MalformedEvent:
    """Build a :class:`MalformedEvent` with truncation handling."""
    raw_str = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
    truncated = len(raw_str.encode("utf-8")) > MAX_RAW_PAYLOAD_BYTES
    if truncated:
        raw_str = raw_str[:MAX_RAW_PAYLOAD_BYTES]

    return MalformedEvent(
        event_type="malformed",
        timestamp=now,
        received_at=now,
        error={
            "code": code,
            "message": message,
            "raw_payload": raw_str,
            "raw_payload_truncated": truncated,
        },
        source=source,
    )
