"""Output sinks: rotating NDJSON files and stdout (for debugging/dry-run).

FileSink
    Writes to ``{prefix}-{instance_id}-{timestamp}.ndjson.active``.
    Rotates when a time or size threshold is reached — ``fsync``, atomic
    ``os.rename`` to ``.ndjson``, then open a new ``.active`` file.  The
    sink does **not** handle compression, retention, or disk pressure.

StdoutSink
    Writes raw bytes to ``sys.stdout.buffer``.  Useful for debugging and
    dry-run validation.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class StdoutSink:
    """Write NDJSON bytes directly to stdout (for debugging and dry-run)."""

    def write(self, data: bytes) -> None:
        """Write *data* to ``sys.stdout.buffer``.

        Raises
        ------
        BrokenPipeError
            If the stdout consumer has gone away.
        """
        try:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
        except BrokenPipeError:
            logger.warning("stdout broken — consumer likely exited")
            raise

    def close(self) -> None:
        """No-op for stdout."""


class FileSink:
    """Rotating NDJSON file writer.

    Parameters
    ----------
    output_dir:
        Directory for output files.
    prefix:
        Filename prefix (e.g. ``"events"``).
    instance_id:
        Unique instance identifier included in the filename.
    rotation_seconds:
        Rotate after this many seconds.
    rotation_bytes:
        Rotate after the active file reaches this size.
    flush_every_n:
        Flush the write buffer after this many events.
    flush_interval_ms:
        Flush the write buffer after this many milliseconds.
    """

    def __init__(
        self,
        output_dir: str,
        prefix: str = "events",
        instance_id: str = "writer-01",
        rotation_seconds: int = 600,
        rotation_bytes: int = 52428800,
        flush_every_n: int = 50,
        flush_interval_ms: int = 1000,
    ) -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._prefix = prefix
        self._instance_id = instance_id
        self._rotation_seconds = rotation_seconds
        self._rotation_bytes = rotation_bytes
        self._flush_every_n = flush_every_n
        self._flush_interval_ms = flush_interval_ms

        self._fh = None
        self._active_path: Path | None = None
        self._final_path: Path | None = None
        self._bytes_written = 0
        self._events_since_flush = 0
        self._last_flush_time = time.monotonic()
        self._opened_at = 0.0

        self._open_new_file()

    # ── public API ──────────────────────────────────────────────────

    def write(self, data: bytes) -> None:
        """Append *data* to the active file, rotating if thresholds are met."""
        if self._should_rotate():
            self._rotate()

        self._fh.write(data)
        self._bytes_written += len(data)
        self._events_since_flush += 1

        if self._should_flush():
            self._flush()

    def close(self) -> None:
        """Flush, fsync, and rename the active file on graceful shutdown."""
        if self._fh and not self._fh.closed:
            self._flush()
            os.fsync(self._fh.fileno())
            self._fh.close()
            if self._active_path and self._active_path.exists():
                os.rename(self._active_path, self._final_path)
                logger.info(
                    "Closed and renamed %s → %s",
                    self._active_path.name,
                    self._final_path.name,
                )

    # ── internal ────────────────────────────────────────────────────

    def _open_new_file(self) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base = f"{self._prefix}-{self._instance_id}-{ts}"
        self._active_path = self._output_dir / f"{base}.ndjson.active"
        self._final_path = self._output_dir / f"{base}.ndjson"
        self._fh = open(self._active_path, "ab")
        self._bytes_written = 0
        self._events_since_flush = 0
        self._opened_at = time.monotonic()
        self._last_flush_time = time.monotonic()
        logger.info("Opened new file: %s", self._active_path.name)

    def _should_rotate(self) -> bool:
        elapsed = time.monotonic() - self._opened_at
        return (
            self._bytes_written >= self._rotation_bytes
            or elapsed >= self._rotation_seconds
        )

    def _rotate(self) -> None:
        self._flush()
        os.fsync(self._fh.fileno())
        self._fh.close()
        os.rename(self._active_path, self._final_path)
        logger.info(
            "Rotated %s (%d bytes)",
            self._final_path.name,
            self._bytes_written,
        )
        self._open_new_file()

    def _should_flush(self) -> bool:
        if self._events_since_flush >= self._flush_every_n:
            return True
        elapsed_ms = (time.monotonic() - self._last_flush_time) * 1000
        return elapsed_ms >= self._flush_interval_ms

    def _flush(self) -> None:
        if self._fh and not self._fh.closed:
            self._fh.flush()
            self._events_since_flush = 0
            self._last_flush_time = time.monotonic()
