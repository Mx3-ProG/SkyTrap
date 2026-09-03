import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from skytrap.core.context import detect_workspace
from skytrap.memory.sqlite import SqliteMemory
from skytrap.models.ollama import OllamaProvider
from skytrap.server.agent_worker import run_server_task
from skytrap.server.auth.dependencies import get_current_user_id

router = APIRouter(tags=["turns"])


class TurnRequest(BaseModel):
    task: str
    workspace: str


@router.post("/turns")
def create_turn(
    payload: TurnRequest, request: Request, user_id: int = Depends(get_current_user_id)
) -> dict:
    connection_manager = request.app.state.connection_manager
    if connection_manager.bridge is None:
        raise HTTPException(
            status_code=409, detail="No active WebSocket connection to stream progress/confirmations to"
        )

    registry = request.app.state.turn_registry
    turn = registry.create()
    workspace = detect_workspace(Path(payload.workspace))
    model = request.app.state.model_provider or OllamaProvider()
    bridge = connection_manager.bridge
    memory_db_path = request.app.state.memory_db_path

    def worker() -> None:
        # SqliteMemory wraps a sqlite3 connection that must be created and used on
        # the same thread — created here (inside the worker thread itself), not in
        # the request handler, otherwise sqlite3 raises ProgrammingError.
        memory = SqliteMemory(memory_db_path)
        session_id = memory.start_session(str(workspace.path))
        try:

            def on_progress(step: dict) -> None:
                connection_manager.send_from_worker_thread({"type": "turn_progress", "turn_id": turn.id, **step})

            run_server_task(
                turn.id, payload.task, model, workspace, registry, on_progress, bridge, memory, session_id
            )
        finally:
            final = registry.get(turn.id)
            if final is not None:
                connection_manager.send_from_worker_thread(
                    {
                        "type": "turn_complete",
                        "turn_id": turn.id,
                        "status": final.status,
                        "result": final.result,
                        "error": final.error,
                    }
                )
            memory.close()

    threading.Thread(target=worker, daemon=True).start()
    return {"turn_id": turn.id}


@router.get("/turns/{turn_id}")
def get_turn(turn_id: str, request: Request, user_id: int = Depends(get_current_user_id)) -> dict:
    registry = request.app.state.turn_registry
    turn = registry.get(turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail="Unknown turn")
    return {"turn_id": turn.id, "status": turn.status, "result": turn.result, "error": turn.error}
