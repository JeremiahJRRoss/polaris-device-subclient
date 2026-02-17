"""Click CLI for Polaris Device Subclient.

Entry point registered in ``pyproject.toml`` as ``polaris-device-subclient``.

Subcommands::

    polaris-device-subclient                  # run the pipeline (default: file output)
    polaris-device-subclient secrets init     # create encrypted secrets file
    polaris-device-subclient secrets set KEY  # store a secret
    polaris-device-subclient secrets list     # list secret names
    polaris-device-subclient secrets rekey    # re-encrypt with a new key
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import click
import orjson

from polaris_device_subclient import __version__
from polaris_device_subclient.classifier import classify
from polaris_device_subclient.config import AppConfig, load_config
from polaris_device_subclient.connection import PolarisConnection
from polaris_device_subclient.filter import EventFilter
from polaris_device_subclient.models import MalformedEvent
from polaris_device_subclient.output import FileSink, StdoutSink
from polaris_device_subclient.redactor import SecretRedactingFilter, collect_secret_values
from polaris_device_subclient.transform import Transformer

logger = logging.getLogger("polaris_device_subclient")

DEFAULT_CONFIG = "/etc/polaris/config.json"


# ── structured JSON log formatter ───────────────────────────────────


class _JsonFormatter(logging.Formatter):
    """Emit log records as single-line JSON to stderr."""

    def format(self, record: logging.LogRecord) -> str:
        obj = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            obj["exception"] = self.formatException(record.exc_info)
        return orjson.dumps(obj).decode()


def _setup_logging(
    level: str,
    secret_values: list[str] | None = None,
    log_file_config: Optional["LogFileConfig"] = None,
) -> None:
    """Configure the root logger with JSON output on stderr + optional file + redaction."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Always log to stderr (journald picks this up)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(_JsonFormatter())
    root.addHandler(stderr_handler)

    # Optionally log to a rotating file
    if log_file_config and log_file_config.enabled:
        from logging.handlers import RotatingFileHandler

        log_dir = Path(log_file_config.path).parent
        log_dir.mkdir(parents=True, exist_ok=True)

        file_handler = RotatingFileHandler(
            filename=log_file_config.path,
            maxBytes=log_file_config.max_size_bytes,
            backupCount=log_file_config.backup_count,
        )
        file_handler.setFormatter(_JsonFormatter())
        root.addHandler(file_handler)

    redactor = SecretRedactingFilter(secret_values)
    root.addFilter(redactor)


# ── main CLI group ──────────────────────────────────────────────────


@click.group(invoke_without_command=True)
@click.option("-o", "--output", "output_mode", type=click.Choice(["stdout", "file"]),
              default=None, help="Output mode (default: file).")
@click.option("-d", "--output-dir", default=None, help="Override output directory.")
@click.option("-c", "--config", "config_path", default=None,
              help="Config file path.")
@click.option("--log-level", default=None,
              type=click.Choice(["debug", "info", "warn", "error"]),
              help="Log verbosity.")
@click.option("--dry-run", is_flag=True, help="Receive ~5 events then exit.")
@click.option("--validate-config", "validate_only", is_flag=True,
              help="Validate config and exit.")
@click.option("--polaris-api-key", default=None, help="Override API key.")
@click.option("--polaris-api-url", default=None, help="Override API URL.")
@click.version_option(__version__)
@click.pass_context
def main(
    ctx: click.Context,
    output_mode: Optional[str],
    output_dir: Optional[str],
    config_path: Optional[str],
    log_level: Optional[str],
    dry_run: bool,
    validate_only: bool,
    polaris_api_key: Optional[str],
    polaris_api_url: Optional[str],
) -> None:
    """Polaris Device Subclient — device state change to NDJSON file pipeline."""
    if ctx.invoked_subcommand is not None:
        return  # defer to subcommand

    # --- resolve config path ---
    import os
    cfg_path = config_path or os.environ.get("POLARIS_CONFIG", DEFAULT_CONFIG)

    # --- build overrides ---
    overrides: dict[str, str] = {}
    if polaris_api_key:
        overrides["POLARIS_API_KEY"] = polaris_api_key
    if polaris_api_url:
        overrides["POLARIS_API_URL"] = polaris_api_url

    # --- load encrypted secrets if key file is available ---
    secrets_dict: dict[str, str] = {}
    key_file = os.environ.get("POLARIS_KEY_FILE")
    secrets_file = os.environ.get(
        "POLARIS_SECRETS_FILE", "/etc/polaris/.secrets.enc"
    )
    if key_file and Path(key_file).exists() and Path(secrets_file).exists():
        from polaris_device_subclient.secrets import load_secrets
        secrets_dict = load_secrets(secrets_file, key_file)

    # --- load + validate config ---
    try:
        cfg = load_config(cfg_path, overrides=overrides, secrets=secrets_dict)
    except Exception as exc:
        click.echo(f"Config error: {exc}", err=True)
        raise SystemExit(1) from exc

    # --- resolve runtime overrides ---
    effective_level = (
        log_level
        or os.environ.get("POLARIS_LOG_LEVEL")
        or cfg.logging.level
    )
    effective_output = (
        output_mode
        or os.environ.get("POLARIS_OUTPUT")
        or "file"
    )
    if output_dir:
        cfg.output.file.output_dir = output_dir
    elif os.environ.get("POLARIS_OUTPUT_DIR"):
        cfg.output.file.output_dir = os.environ["POLARIS_OUTPUT_DIR"]

    # --- setup logging with secret redaction ---
    secret_values = collect_secret_values(
        asdict(cfg) if hasattr(cfg, '__dataclass_fields__') else {},
        cfg.logging.redact_patterns,
    )
    _setup_logging(effective_level, secret_values, cfg.logging.file)

    if validate_only:
        click.echo("Configuration is valid.", err=True)
        raise SystemExit(0)

    logger.info(
        "Starting polaris-device-subclient %s (instance=%s, output=%s)",
        __version__,
        cfg.instance_id,
        effective_output,
    )

    # --- run the pipeline ---
    asyncio.run(_run_pipeline(cfg, effective_output, dry_run))


# ── async pipeline ──────────────────────────────────────────────────


async def _run_pipeline(cfg: AppConfig, output_mode: str, dry_run: bool) -> None:
    """Core async event loop: connect → classify → filter → transform → output."""
    loop = asyncio.get_running_loop()

    conn = PolarisConnection(cfg.polaris)
    filt = EventFilter(cfg.filter)
    xform = Transformer(
        instance_id=cfg.instance_id,
        subscription_id=None,  # updated once connected
    )

    if output_mode == "stdout":
        sink = StdoutSink()
    else:
        fc = cfg.output.file
        sink = FileSink(
            output_dir=fc.output_dir,
            prefix=fc.file_prefix,
            instance_id=cfg.instance_id,
            rotation_seconds=fc.rotation.interval_seconds,
            rotation_bytes=fc.rotation.max_size_bytes,
            flush_every_n=fc.flush.every_n_events,
            flush_interval_ms=fc.flush.interval_ms,
        )

    # --- signal handling ---
    def _handle_signal() -> None:
        logger.info("Received shutdown signal")
        conn.request_shutdown()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            pass  # Windows

    event_count = 0
    try:
        async for raw_message in conn.subscribe():
            result = classify(
                raw_message,
                instance_id=cfg.instance_id,
                subscription_id=conn.subscription_id,
            )

            if result is None:
                continue  # protocol message

            if isinstance(result, MalformedEvent):
                data = xform.transform_malformed(result)
            else:
                filtered = filt.apply(result)
                if filtered is None:
                    continue
                data = xform.transform(filtered)

            try:
                sink.write(data)
            except BrokenPipeError:
                break

            event_count += 1
            if dry_run and event_count >= 5:
                logger.info("Dry run complete — received %d events", event_count)
                break
    finally:
        if hasattr(sink, "close"):
            sink.close()
        logger.info("Pipeline shut down (processed %d events)", event_count)


# ── secrets subcommand group ────────────────────────────────────────


@main.group()
def secrets() -> None:
    """Manage the encrypted secrets file."""


@secrets.command("init")
@click.option("--output", required=True, help="Path for the encrypted file.")
@click.option("--key-file", required=True, help="Path for the master key.")
def secrets_init(output: str, key_file: str) -> None:
    """Create an empty encrypted secrets file and key."""
    from polaris_device_subclient.secrets import init_secrets
    init_secrets(output, key_file)
    click.echo(f"Initialized: {output} (key: {key_file})")


@secrets.command("set")
@click.argument("key")
@click.option("--value", required=True, help="Secret value.")
@click.option("--key-file", required=True, help="Path to the master key.")
def secrets_set(key: str, value: str, key_file: str) -> None:
    """Store a secret in the encrypted file."""
    import os
    from polaris_device_subclient.secrets import set_secret
    sf = os.environ.get("POLARIS_SECRETS_FILE", "/etc/polaris/.secrets.enc")
    set_secret(sf, key_file, key, value)
    click.echo(f"Set: {key}")


@secrets.command("list")
@click.option("--key-file", required=True, help="Path to the master key.")
def secrets_list(key_file: str) -> None:
    """List stored secret names (values are never shown)."""
    import os
    from polaris_device_subclient.secrets import list_secrets
    sf = os.environ.get("POLARIS_SECRETS_FILE", "/etc/polaris/.secrets.enc")
    for name in list_secrets(sf, key_file):
        click.echo(name)


@secrets.command("rekey")
@click.option("--key-file", required=True, help="Current master key path.")
@click.option("--new-key-file", required=True, help="New master key path.")
def secrets_rekey(key_file: str, new_key_file: str) -> None:
    """Re-encrypt the secrets store with a new key."""
    import os
    from polaris_device_subclient.secrets import rekey
    sf = os.environ.get("POLARIS_SECRETS_FILE", "/etc/polaris/.secrets.enc")
    rekey(sf, key_file, new_key_file)
    click.echo(f"Re-keyed with: {new_key_file}")
