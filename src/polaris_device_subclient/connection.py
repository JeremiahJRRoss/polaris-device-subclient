"""WebSocket connection to Polaris GraphQL subscription API.

Implements the ``graphql-ws`` protocol over WSS with an exponential-backoff
reconnect state machine::

    INIT → CONNECTING → (success) → CONNECTED → (disconnect) → WAIT_BACKOFF → CONNECTING
                      → (failure) →              WAIT_BACKOFF → CONNECTING
    CONNECTED → (SIGTERM) → SHUTTING_DOWN

Exposed as an async generator via :meth:`PolarisConnection.subscribe`.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import random
import uuid
from typing import AsyncIterator, Optional

import orjson
import websockets
import websockets.exceptions

from polaris_device_subclient.config import PolarisConfig

logger = logging.getLogger(__name__)

DEVICES_SUBSCRIPTION = """\
subscription DevicesSubscription {
  devices {
    id
    label
    tags {
      key
      value
    }
    lastPosition {
      position {
        llaDec {
          lat
          lon
          alt
        }
      }
      timestamp
    }
    services {
      rtk {
        enabled
        connectionStatus
      }
    }
  }
}"""


class ConnectionState(enum.Enum):
    """States in the reconnect state machine."""

    INIT = "INIT"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    WAIT_BACKOFF = "WAIT_BACKOFF"
    SHUTTING_DOWN = "SHUTTING_DOWN"


class PolarisConnection:
    """Manages the WSS lifecycle and ``graphql-ws`` protocol.

    Parameters
    ----------
    config:
        Polaris connection settings (URL, API key, reconnect params).
    """

    def __init__(self, config: PolarisConfig) -> None:
        self._url = config.api_url
        self._api_key = config.api_key
        self._reconnect = config.reconnect
        self._state = ConnectionState.INIT
        self._shutdown = asyncio.Event()
        self._ws = None
        self._attempt = 0
        self._subscription_id: Optional[str] = None

    @property
    def subscription_id(self) -> Optional[str]:
        """The current subscription message ID (set after subscribe)."""
        return self._subscription_id

    def request_shutdown(self) -> None:
        """Signal the connection to close gracefully (no reconnect)."""
        self._set_state(ConnectionState.SHUTTING_DOWN)
        self._shutdown.set()

    async def subscribe(self) -> AsyncIterator[str]:
        """Async generator that yields raw ``next`` payload strings.

        Handles connection, authentication, subscription, and automatic
        reconnection with exponential backoff.  Stops when
        :meth:`request_shutdown` is called.

        Yields
        ------
        str
            The raw JSON string of each WebSocket message received.
        """
        while not self._shutdown.is_set():
            try:
                async for raw_message in self._connect_and_receive():
                    yield raw_message
            except _FatalAuthError:
                logger.error("Fatal auth error — will not reconnect")
                break
            except Exception as exc:
                if self._shutdown.is_set():
                    break
                logger.warning("Connection error: %s", exc)

            if self._shutdown.is_set():
                break

            await self._backoff()

    # ── internal: connect + receive ─────────────────────────────────

    async def _connect_and_receive(self) -> AsyncIterator[str]:
        """Open WSS, authenticate, subscribe, and yield raw messages."""
        self._set_state(ConnectionState.CONNECTING)
        self._subscription_id = str(uuid.uuid4())

        try:
            async with websockets.connect(
                self._url,
                subprotocols=["graphql-transport-ws"],
                ping_interval=20,
                ping_timeout=20,
                close_timeout=10,
            ) as ws:
                self._ws = ws

                # 1. connection_init
                await ws.send(orjson.dumps({
                    "type": "connection_init",
                    "payload": {"Authorization": f"Bearer {self._api_key}"},
                }).decode())

                # 2. wait for connection_ack (10 s timeout)
                ack_raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                ack = orjson.loads(ack_raw)
                if ack.get("type") != "connection_ack":
                    logger.error("Expected connection_ack, got: %s", ack.get("type"))
                    if ack.get("type") == "error":
                        raise _FatalAuthError("Auth rejected by server")
                    raise ConnectionError("No connection_ack received")

                # 3. subscribe
                await ws.send(orjson.dumps({
                    "id": self._subscription_id,
                    "type": "subscribe",
                    "payload": {"query": DEVICES_SUBSCRIPTION},
                }).decode())

                self._set_state(ConnectionState.CONNECTED)
                self._attempt = 0  # reset backoff on success

                # 4. receive loop
                async for raw in ws:
                    if self._shutdown.is_set():
                        break

                    try:
                        msg = orjson.loads(raw)
                    except Exception:
                        # Yield raw for the classifier to handle
                        yield raw if isinstance(raw, str) else raw.decode()
                        continue

                    msg_type = msg.get("type", "")

                    if msg_type == "next":
                        yield raw if isinstance(raw, str) else raw.decode()
                    elif msg_type == "error":
                        logger.error("Subscription error: %s", msg.get("payload"))
                        payload = msg.get("payload", [])
                        if isinstance(payload, list):
                            for err in payload:
                                code = (err.get("extensions") or {}).get("code", "")
                                if code in ("FORBIDDEN", "UNAUTHORIZED"):
                                    raise _FatalAuthError(err.get("message", ""))
                        yield raw if isinstance(raw, str) else raw.decode()
                    elif msg_type == "complete":
                        logger.info("Subscription completed by server — will reconnect")
                        break
                    elif msg_type == "ping":
                        await ws.send(orjson.dumps({"type": "pong"}).decode())
                    # ignore connection_ack and other types

        except _FatalAuthError:
            raise
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for connection_ack")
        except websockets.exceptions.ConnectionClosed as exc:
            logger.warning("WebSocket closed: %s", exc)
        except OSError as exc:
            logger.warning("Network error: %s", exc)
        finally:
            self._ws = None

    # ── backoff ─────────────────────────────────────────────────────

    async def _backoff(self) -> None:
        """Wait with exponential backoff + jitter before reconnecting."""
        self._set_state(ConnectionState.WAIT_BACKOFF)
        self._attempt += 1

        base = self._reconnect.initial_delay_ms / 1000.0
        multiplier = self._reconnect.backoff_multiplier
        max_delay = self._reconnect.max_delay_ms / 1000.0
        jitter_pct = self._reconnect.jitter_pct / 100.0

        delay = min(base * (multiplier ** (self._attempt - 1)), max_delay)
        jitter = delay * jitter_pct * (2 * random.random() - 1)
        delay = max(0.1, delay + jitter)

        logger.info(
            "Reconnecting in %.1fs (attempt %d)",
            delay,
            self._attempt,
        )

        try:
            await asyncio.wait_for(self._shutdown.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass  # backoff elapsed normally

    # ── helpers ─────────────────────────────────────────────────────

    def _set_state(self, new: ConnectionState) -> None:
        old = self._state
        self._state = new
        logger.info("Connection state: %s → %s", old.value, new.value)


class _FatalAuthError(Exception):
    """Raised when the server rejects authentication — no reconnect."""
