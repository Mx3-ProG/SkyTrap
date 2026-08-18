import asyncio
import threading
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

DEFAULT_TIMEOUT_SECONDS = 300.0


@dataclass
class PendingConfirmation:
    request_id: str
    event: threading.Event
    answer: bool | None = None


class ConfirmationBridge:
    """Bridges a synchronous confirm() call happening on a worker thread (inside a
    Tool.execute(), exactly like the existing confirm_write/confirm_shell callbacks
    in ui/terminal.py) to an async WebSocket round-trip on the event loop thread.

    One instance per WebSocket connection/session — created when the socket
    connects (so it can capture that connection's event loop and send function),
    discarded when it disconnects.
    """

    def __init__(
        self,
        send_to_client: Callable[[dict], Awaitable[None]],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._pending: dict[str, PendingConfirmation] = {}
        self._lock = threading.Lock()
        self._send = send_to_client
        self._loop = loop

    def request(
        self, preview: str, kind: str, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    ) -> bool:
        """Called ON THE WORKER THREAD, from inside a Tool.execute(). Blocks until
        answered, timed out, or abandoned (WebSocket disconnects). Never raises —
        timeout and abandonment both resolve to False (decline), never to a silent
        approval.
        """
        request_id = str(uuid.uuid4())
        pending = PendingConfirmation(request_id, threading.Event())
        with self._lock:
            self._pending[request_id] = pending

        # asyncio objects aren't thread-safe to touch directly from a worker thread —
        # this schedules the actual send onto the event loop that owns them.
        asyncio.run_coroutine_threadsafe(
            self._send({"type": "confirm_request", "id": request_id, "kind": kind, "preview": preview}),
            self._loop,
        )

        answered = pending.event.wait(timeout=timeout_seconds)
        with self._lock:
            self._pending.pop(request_id, None)

        return bool(pending.answer) if answered else False

    def resolve(self, request_id: str, answer: bool) -> bool:
        """Called FROM THE EVENT LOOP (the WebSocket receive handler) when the
        client replies. Returns False (and does nothing) if request_id is unknown —
        already timed out, already resolved, or abandoned; a stale reply must never
        raise or corrupt a newer, unrelated pending confirmation.
        """
        with self._lock:
            pending = self._pending.get(request_id)
        if pending is None:
            return False
        pending.answer = answer
        pending.event.set()
        return True

    def abandon_all(self) -> None:
        """Called when the WebSocket disconnects. Unblocks every pending worker
        thread immediately with a decline, rather than letting them sit blocked for
        the full timeout on a connection that's already gone.
        """
        with self._lock:
            items = list(self._pending.values())
        for pending in items:
            pending.answer = False
            pending.event.set()
