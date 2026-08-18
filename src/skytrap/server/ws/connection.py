import asyncio
from typing import Awaitable, Callable

from skytrap.server.ws.confirmation_bridge import ConfirmationBridge


class ConnectionManager:
    """Tracks the single active authenticated WebSocket connection and its
    ConfirmationBridge. Decided at milestone 4 (§3.6): SkyTrap is single-user, so
    "one active connection" is a deliberate simplification, not a limitation to
    work around — a second connection replaces the first rather than coexisting
    with it, since there's only ever one person to confirm anything.
    """

    def __init__(self) -> None:
        self.bridge: ConfirmationBridge | None = None
        self._send_to_client: Callable[[dict], Awaitable[None]] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def connect(
        self, send_to_client: Callable[[dict], Awaitable[None]], loop: asyncio.AbstractEventLoop
    ) -> ConfirmationBridge:
        bridge = ConfirmationBridge(send_to_client=send_to_client, loop=loop)
        self.bridge = bridge
        self._send_to_client = send_to_client
        self._loop = loop
        return bridge

    def disconnect(self, bridge: ConfirmationBridge) -> None:
        # Only clear if this disconnect is for the currently active bridge — a
        # stale disconnect from a superseded connection must not wipe out a newer,
        # still-active one.
        if self.bridge is bridge:
            bridge.abandon_all()
            self.bridge = None
            self._send_to_client = None
            self._loop = None

    def send_from_worker_thread(self, message: dict) -> None:
        """Called from a background turn thread (never the event loop thread) to
        push a message (turn_progress/turn_complete) to the active connection, if
        any — same asyncio.run_coroutine_threadsafe bridge pattern as
        ConfirmationBridge.request(). Silently drops the message if no connection
        is active (e.g. the client disconnected mid-turn); the turn itself keeps
        running and its final status is still recoverable via GET /turns/{id}.
        """
        if self._send_to_client is None or self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._send_to_client(message), self._loop)
