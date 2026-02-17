"""Tests for the output module (StdoutSink and FileSink)."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from polaris_device_subclient.output import FileSink, StdoutSink


class TestStdoutSink:
    """Tests for :class:`StdoutSink`."""

    def test_stdout_sink_writes_bytes(self) -> None:
        """StdoutSink writes raw bytes to stdout buffer."""
        sink = StdoutSink()
        data = b'{"event_type":"state_change"}\n'

        mock_buffer = MagicMock()
        mock_stdout = MagicMock()
        mock_stdout.buffer = mock_buffer
        with patch("polaris_device_subclient.output.sys") as mock_sys:
            mock_sys.stdout = mock_stdout
            sink.write(data)
            mock_buffer.write.assert_called_once_with(data)
            mock_buffer.flush.assert_called_once()


class TestFileSink:
    """Tests for :class:`FileSink`."""

    def test_file_sink_creates_active_file(self, tmp_path: Path) -> None:
        """FileSink creates a ``.ndjson.active`` file on init."""
        sink = FileSink(
            output_dir=str(tmp_path),
            prefix="test",
            instance_id="inst-01",
            rotation_seconds=3600,
            rotation_bytes=1_000_000,
        )
        try:
            active_files = list(tmp_path.glob("*.ndjson.active"))
            assert len(active_files) == 1
            assert "test-inst-01" in active_files[0].name
        finally:
            sink.close()

    def test_file_sink_rotation(self, tmp_path: Path) -> None:
        """After exceeding size, old file is renamed to .ndjson and new .active is created."""
        sink = FileSink(
            output_dir=str(tmp_path),
            prefix="test",
            instance_id="inst-01",
            rotation_seconds=3600,
            rotation_bytes=100,  # very small — triggers on next write
        )
        try:
            # Write enough to exceed rotation threshold
            sink.write(b"x" * 110)
            # Next write triggers rotation
            sink.write(b"y" * 10)

            ndjson_files = list(tmp_path.glob("*.ndjson"))
            active_files = list(tmp_path.glob("*.ndjson.active"))

            # Should have at least 1 completed file and 1 active file
            assert len(ndjson_files) >= 1, "Expected at least one completed .ndjson file"
            assert len(active_files) == 1, "Expected exactly one .active file"
        finally:
            sink.close()

    def test_file_sink_close_renames(self, tmp_path: Path) -> None:
        """close() renames ``.active`` to ``.ndjson``."""
        sink = FileSink(
            output_dir=str(tmp_path),
            prefix="test",
            instance_id="inst-01",
            rotation_seconds=3600,
            rotation_bytes=1_000_000,
        )
        sink.write(b'{"test": true}\n')
        sink.close()

        active_files = list(tmp_path.glob("*.ndjson.active"))
        ndjson_files = list(tmp_path.glob("*.ndjson"))

        assert len(active_files) == 0, "No .active files should remain after close()"
        assert len(ndjson_files) == 1, "Should have exactly one .ndjson file after close()"
