"""Event filtering by RTK connection status and device identity.

Filter chain (evaluated in order)::

    1. ``services.rtk.connectionStatus`` in ``drop_states``   → drop
    2. ``id`` in ``drop_device_ids``                          → drop
    3. ``keep_device_ids`` non-empty AND ``id`` not in list   → drop
    4. Otherwise                                              → pass
"""

from __future__ import annotations

import logging
from typing import Optional

from polaris_device_subclient.config import FilterConfig

logger = logging.getLogger(__name__)


class EventFilter:
    """Stateless filter that decides whether a device event passes through."""

    def __init__(self, config: FilterConfig) -> None:
        self._drop_states: set[str] = set(config.drop_states)
        self._drop_device_ids: set[str] = set(config.drop_device_ids)
        self._keep_device_ids: set[str] = set(config.keep_device_ids)

    def __call__(self, devices: dict) -> Optional[dict]:
        """Return *devices* if it passes all filters, else ``None``."""
        return self.apply(devices)

    def apply(self, devices: dict) -> Optional[dict]:
        """Evaluate the filter chain.

        Parameters
        ----------
        devices:
            Validated device dict from the classifier.

        Returns
        -------
        dict or None
            The input dict unchanged when it passes, ``None`` when filtered.
        """
        # 1. Drop by RTK connection status
        connection_status = _get_connection_status(devices)
        if connection_status is not None and connection_status in self._drop_states:
            logger.debug(
                "Filtered device %s: state %s in drop_states",
                devices.get("id"),
                connection_status,
            )
            return None

        device_id = devices.get("id", "")

        # 2. Drop by explicit device ID deny-list
        if device_id in self._drop_device_ids:
            logger.debug("Filtered device %s: in drop_device_ids", device_id)
            return None

        # 3. Keep-list (allow-list)
        if self._keep_device_ids and device_id not in self._keep_device_ids:
            logger.debug("Filtered device %s: not in keep_device_ids", device_id)
            return None

        return devices


def _get_connection_status(devices: dict) -> Optional[str]:
    """Safely extract ``services.rtk.connectionStatus``."""
    try:
        return devices["services"]["rtk"]["connectionStatus"]
    except (KeyError, TypeError):
        return None
