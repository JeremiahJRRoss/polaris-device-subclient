"""Dataclass models for Polaris Device Subclient events.

All models are designed to be serializable via ``dataclasses.asdict()``
followed by ``orjson.dumps()``.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SourceInfo:
    """Provenance metadata attached to every output record."""

    instance_id: str = ""
    subscription_id: Optional[str] = None


@dataclass
class StateChangeEvent:
    """A device RTK connection-status transition.

    Emitted whenever the classifier yields a valid device payload whose
    ``services.rtk.connectionStatus`` is present.
    """

    event_type: str = "state_change"
    timestamp: Optional[str] = None
    received_at: str = ""
    device_id: str = ""
    device_label: Optional[str] = None
    previous_state: Optional[str] = None
    current_state: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude_m: Optional[float] = None
    rtk_enabled: Optional[bool] = None
    tags: Optional[list] = None
    source: Optional[dict] = field(default_factory=dict)


@dataclass
class ErrorDetail:
    """Structured error information for malformed events."""

    code: str = ""
    message: str = ""
    raw_payload: str = ""
    raw_payload_truncated: bool = False


@dataclass
class MalformedEvent:
    """Wrapper for messages that fail classification.

    These are *never* silently dropped — they appear in the NDJSON output
    alongside normal events so operators can monitor data quality.
    """

    event_type: str = "malformed"
    timestamp: str = ""
    received_at: str = ""
    error: Optional[dict] = field(default_factory=dict)
    source: Optional[dict] = field(default_factory=dict)
