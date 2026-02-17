"""Logging filter that redacts known secret values from log records.

At startup the resolved configuration is scanned for values that match
``logging.redact_patterns`` (shell-style globs applied to config *keys*).
Every matching *value* is collected.  Before any log record is emitted the
filter replaces occurrences of these values with ``[REDACTED]``.
"""

from __future__ import annotations

import fnmatch
import logging
from typing import Any, Iterable


REDACTED = "[REDACTED]"


class SecretRedactingFilter(logging.Filter):
    """A :class:`logging.Filter` that scrubs secret values from log output."""

    def __init__(self, secret_values: Iterable[str] | None = None) -> None:
        super().__init__()
        # Only keep non-empty strings that are long enough to be meaningful
        self._secrets: list[str] = [
            s for s in (secret_values or []) if s and len(s) > 1
        ]

    def add_secret(self, value: str) -> None:
        """Register an additional secret value at runtime."""
        if value and len(value) > 1 and value not in self._secrets:
            self._secrets.append(value)

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact secrets in the log record's message and args."""
        if self._secrets:
            record.msg = self._redact(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        k: self._redact(v) if isinstance(v, str) else v
                        for k, v in record.args.items()
                    }
                elif isinstance(record.args, tuple):
                    record.args = tuple(
                        self._redact(a) if isinstance(a, str) else a
                        for a in record.args
                    )
        return True  # never suppress the record itself

    def _redact(self, value: Any) -> Any:
        """Replace all known secret substrings in *value*."""
        if not isinstance(value, str):
            return value
        for secret in self._secrets:
            if secret in value:
                value = value.replace(secret, REDACTED)
        return value


def collect_secret_values(
    config_dict: dict[str, Any],
    patterns: list[str] | None = None,
) -> list[str]:
    """Walk a config dict and collect values whose *keys* match *patterns*.

    Parameters
    ----------
    config_dict:
        Flat or nested configuration dictionary.
    patterns:
        Shell-glob patterns matched against dictionary keys (e.g.
        ``["*key*", "*token*"]``).  Matching is case-insensitive.

    Returns
    -------
    list[str]
        The string values associated with matching keys.
    """
    if not patterns:
        return []

    results: list[str] = []
    _walk(config_dict, patterns, results)
    return results


def _walk(obj: Any, patterns: list[str], out: list[str]) -> None:
    """Recursively collect matching values."""
    if isinstance(obj, dict):
        for key, val in obj.items():
            if isinstance(val, str) and any(
                fnmatch.fnmatch(key.lower(), p.lower()) for p in patterns
            ):
                out.append(val)
            _walk(val, patterns, out)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _walk(item, patterns, out)
