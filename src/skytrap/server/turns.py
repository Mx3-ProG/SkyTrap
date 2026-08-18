import threading
import uuid
from dataclasses import dataclass
from typing import Literal

TurnStatus = Literal["pending", "running", "done", "error"]


@dataclass
class TurnState:
    id: str
    status: TurnStatus = "pending"
    result: str | None = None
    error: str | None = None


class TurnRegistry:
    """In-memory turn tracker, protected by a lock since turns run on background
    threads while HTTP handlers (dispatched to FastAPI's own thread pool) read
    status concurrently. No SQLite persistence yet — deliberately deferred until
    real remote deployment is in play (see milestone 5 plan); a server restart
    losing in-flight turn state is an acceptable gap for now, not a blocker.
    """

    def __init__(self) -> None:
        self._turns: dict[str, TurnState] = {}
        self._lock = threading.Lock()

    def create(self) -> TurnState:
        turn = TurnState(id=str(uuid.uuid4()))
        with self._lock:
            self._turns[turn.id] = turn
        return turn

    def get(self, turn_id: str) -> TurnState | None:
        with self._lock:
            return self._turns.get(turn_id)

    def mark_running(self, turn_id: str) -> None:
        with self._lock:
            turn = self._turns.get(turn_id)
            if turn is not None:
                turn.status = "running"

    def mark_done(self, turn_id: str, result: str) -> None:
        with self._lock:
            turn = self._turns.get(turn_id)
            if turn is not None:
                turn.status = "done"
                turn.result = result

    def mark_error(self, turn_id: str, error: str) -> None:
        with self._lock:
            turn = self._turns.get(turn_id)
            if turn is not None:
                turn.status = "error"
                turn.error = error
