"""Configuration loading, environment-variable interpolation, and validation.

Resolution order for ``${VAR}`` placeholders:
    CLI overrides → environment variables → encrypted secrets → raw config value.

``${VAR}`` (no default) raises if unresolvable.
``${VAR:-default}`` falls back to *default*.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import orjson
import jsonschema

logger = logging.getLogger(__name__)

_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")

_SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "config.schema.json"


@dataclass
class ReconnectConfig:
    """Reconnection backoff parameters."""

    initial_delay_ms: int = 1000
    max_delay_ms: int = 60000
    backoff_multiplier: int = 2
    jitter_pct: int = 20


@dataclass
class PolarisConfig:
    """Polaris API connection settings."""

    api_url: str = "wss://graphql.pointonenav.com/subscriptions"
    api_key: str = ""
    subscription: str = "devices"
    reconnect: ReconnectConfig = field(default_factory=ReconnectConfig)


@dataclass
class RotationConfig:
    """File rotation thresholds."""

    interval_seconds: int = 600
    max_size_bytes: int = 52428800


@dataclass
class FlushConfig:
    """File flush settings."""

    interval_ms: int = 1000
    every_n_events: int = 50


@dataclass
class FileOutputConfig:
    """File-mode output settings."""

    output_dir: str = "/var/lib/polaris/data"
    file_prefix: str = "events"
    rotation: RotationConfig = field(default_factory=RotationConfig)
    flush: FlushConfig = field(default_factory=FlushConfig)


@dataclass
class OutputConfig:
    """Output section wrapper."""

    file: FileOutputConfig = field(default_factory=FileOutputConfig)


@dataclass
class FilterConfig:
    """Event filtering rules."""

    drop_states: list[str] = field(default_factory=lambda: ["undefined", "error"])
    drop_device_ids: list[str] = field(default_factory=list)
    keep_device_ids: list[str] = field(default_factory=list)


@dataclass
class LogFileConfig:
    """Optional log file output settings.

    When ``enabled`` is True the application writes operational logs to a
    rotating file in addition to stderr.
    """

    enabled: bool = False
    path: str = "/var/log/polaris-device-subclient/app.log"
    max_size_bytes: int = 10485760   # 10 MB
    backup_count: int = 5


@dataclass
class LoggingConfig:
    """Logging settings."""

    level: str = "info"
    format: str = "json"
    output: str = "stderr"
    file: LogFileConfig = field(default_factory=LogFileConfig)
    redact_patterns: list[str] = field(
        default_factory=lambda: ["*key*", "*token*", "*secret*", "*password*"]
    )


@dataclass
class AppConfig:
    """Top-level application configuration."""

    instance_id: str = "writer-01"
    polaris: PolarisConfig = field(default_factory=PolarisConfig)
    filter: FilterConfig = field(default_factory=FilterConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def _interpolate_value(
    value: str,
    overrides: dict[str, str] | None = None,
    secrets: dict[str, str] | None = None,
) -> str:
    """Replace ``${VAR}`` / ``${VAR:-default}`` in *value*."""

    def _replacer(match: re.Match) -> str:
        var_name = match.group(1)
        default = match.group(2)  # None when no ``:-`` present

        # 1. CLI overrides
        if overrides and var_name in overrides:
            return overrides[var_name]
        # 2. Environment variables
        env_val = os.environ.get(var_name)
        if env_val is not None:
            return env_val
        # 3. Encrypted secrets
        if secrets and var_name in secrets:
            return secrets[var_name]
        # 4. Default
        if default is not None:
            return default

        raise ValueError(
            f"Required variable ${{{var_name}}} is not set in environment, "
            f"CLI overrides, or encrypted secrets"
        )

    return _VAR_RE.sub(_replacer, value)


def _walk_and_interpolate(
    obj: Any,
    overrides: dict[str, str] | None = None,
    secrets: dict[str, str] | None = None,
) -> Any:
    """Recursively interpolate all string values in a JSON-like structure."""
    if isinstance(obj, str):
        return _interpolate_value(obj, overrides, secrets)
    if isinstance(obj, dict):
        return {k: _walk_and_interpolate(v, overrides, secrets) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk_and_interpolate(item, overrides, secrets) for item in obj]
    return obj


def _dict_to_config(raw: dict[str, Any]) -> AppConfig:
    """Convert a raw dict into a typed :class:`AppConfig`."""
    polaris_raw = raw.get("polaris", {})
    reconnect_raw = polaris_raw.get("reconnect", {})
    filter_raw = raw.get("filter", {})
    output_raw = raw.get("output", {})
    file_raw = output_raw.get("file", {})
    rotation_raw = file_raw.get("rotation", {})
    flush_raw = file_raw.get("flush", {})
    logging_raw = raw.get("logging", {})
    log_file_raw = logging_raw.get("file", {})

    return AppConfig(
        instance_id=raw.get("instance_id", "writer-01"),
        polaris=PolarisConfig(
            api_url=polaris_raw.get(
                "api_url", "wss://graphql.pointonenav.com/subscriptions"
            ),
            api_key=polaris_raw.get("api_key", ""),
            subscription=polaris_raw.get("subscription", "devices"),
            reconnect=ReconnectConfig(**{
                k: reconnect_raw[k] for k in reconnect_raw
                if k in ReconnectConfig.__dataclass_fields__
            }),
        ),
        filter=FilterConfig(
            drop_states=filter_raw.get("drop_states", ["undefined", "error"]),
            drop_device_ids=filter_raw.get("drop_device_ids", []),
            keep_device_ids=filter_raw.get("keep_device_ids", []),
        ),
        output=OutputConfig(
            file=FileOutputConfig(
                output_dir=file_raw.get("output_dir", "/var/lib/polaris/data"),
                file_prefix=file_raw.get("file_prefix", "events"),
                rotation=RotationConfig(**{
                    k: rotation_raw[k] for k in rotation_raw
                    if k in RotationConfig.__dataclass_fields__
                }),
                flush=FlushConfig(**{
                    k: flush_raw[k] for k in flush_raw
                    if k in FlushConfig.__dataclass_fields__
                }),
            ),
        ),
        logging=LoggingConfig(
            level=logging_raw.get("level", "info"),
            format=logging_raw.get("format", "json"),
            output=logging_raw.get("output", "stderr"),
            file=LogFileConfig(**{
                k: log_file_raw[k] for k in log_file_raw
                if k in LogFileConfig.__dataclass_fields__
            }),
            redact_patterns=logging_raw.get(
                "redact_patterns",
                ["*key*", "*token*", "*secret*", "*password*"],
            ),
        ),
    )


def load_config(
    path: str | Path,
    overrides: dict[str, str] | None = None,
    secrets: dict[str, str] | None = None,
    schema_path: str | Path | None = None,
) -> AppConfig:
    """Load, interpolate, validate, and return the application config.

    Parameters
    ----------
    path:
        Filesystem path to ``config.json``.
    overrides:
        CLI-supplied variable overrides.
    secrets:
        Values from the encrypted secrets file.
    schema_path:
        Path to the JSON Schema file.  Defaults to
        ``config/config.schema.json`` relative to the project root.

    Returns
    -------
    AppConfig
        Fully resolved and validated configuration.

    Raises
    ------
    ValueError
        If a required ``${VAR}`` cannot be resolved.
    jsonschema.ValidationError
        If the config fails schema validation.
    """
    raw_bytes = Path(path).read_bytes()
    raw: dict[str, Any] = orjson.loads(raw_bytes)

    interpolated = _walk_and_interpolate(raw, overrides=overrides, secrets=secrets)

    # --- schema validation ---
    sp = Path(schema_path) if schema_path else _SCHEMA_PATH
    if sp.exists():
        schema = orjson.loads(sp.read_bytes())
        jsonschema.validate(instance=interpolated, schema=schema)
        logger.debug("Config passed schema validation")
    else:
        logger.warning("Schema file not found at %s — skipping validation", sp)

    return _dict_to_config(interpolated)
