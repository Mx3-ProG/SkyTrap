import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from skytrap.autonomy.approval import ApprovalRequest
from skytrap.autonomy.service import AutonomousTaskService
from skytrap.autonomy.state import TaskStatus
from skytrap.models.ollama import OllamaProvider
from skytrap.server.auth.dependencies import get_current_user_id

router = APIRouter(prefix="/agent/tasks", tags=["agent-tasks"])


class AgentTaskRequest(BaseModel):
    workspace: str
    goal: str
    max_iterations: int = Field(default=20, ge=1, le=200)


class AgentResumeRequest(BaseModel):
    clarification: str | None = None


def _service(request: Request) -> AutonomousTaskService:
    manager = request.app.state.connection_manager
    bridge = manager.bridge

    def approve(approval: ApprovalRequest) -> bool | None:
        if bridge is None:
            return None
        safe_arguments = {
            key: value
            for key, value in approval.arguments.items()
            if key not in {"content", "replacement", "expected"}
        }
        preview = (
            f"Tool: {approval.tool_name}\nRisk: {approval.assessment.level.name}\n"
            f"Arguments: {safe_arguments}"
        )
        return bridge.request(preview, "agent_action")

    def on_event(event: dict) -> None:
        manager.send_from_worker_thread({"type": "agent_task_progress", **event})

    model = request.app.state.model_provider or OllamaProvider()
    return AutonomousTaskService(
        model,
        store=request.app.state.autonomous_task_store,
        approval_callback=approve,
        on_event=on_event,
    )


def _launch(service: AutonomousTaskService, task_id: str, manager) -> None:
    def worker() -> None:
        try:
            task = service.execute(task_id)
            payload = {
                "type": "agent_task_complete",
                "task": task.model_dump(mode="json"),
            }
        except Exception as exc:  # noqa: BLE001 - background failures must reach the client
            payload = {"type": "agent_task_error", "task_id": task_id, "error": str(exc)}
        manager.send_from_worker_thread(payload)

    threading.Thread(target=worker, daemon=True).start()


@router.post("")
def create_agent_task(
    payload: AgentTaskRequest,
    request: Request,
    user_id: int = Depends(get_current_user_id),
) -> dict:
    manager = request.app.state.connection_manager
    if manager.bridge is None:
        raise HTTPException(status_code=409, detail="An active WebSocket is required")
    service = _service(request)
    task = service.start(Path(payload.workspace), payload.goal, payload.max_iterations)
    if task.status == TaskStatus.BLOCKED:
        raise HTTPException(status_code=409, detail=task.error)
    if task.status == TaskStatus.NEEDS_CLARIFICATION:
        return {
            "task_id": task.task_id,
            "status": task.status.value,
            "clarification_question": task.final_message,
        }
    _launch(service, task.task_id, manager)
    return {"task_id": task.task_id, "status": task.status.value, "branch": task.task_branch}


@router.get("/{task_id}")
def get_agent_task(
    task_id: str,
    request: Request,
    user_id: int = Depends(get_current_user_id),
) -> dict:
    try:
        task = _service(request).status(task_id)
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=404, detail="Unknown autonomous task") from exc
    return task.model_dump(mode="json")


@router.post("/{task_id}/resume")
def resume_agent_task(
    task_id: str,
    request: Request,
    payload: AgentResumeRequest | None = None,
    user_id: int = Depends(get_current_user_id),
) -> dict:
    manager = request.app.state.connection_manager
    if manager.bridge is None:
        raise HTTPException(status_code=409, detail="An active WebSocket is required")
    service = _service(request)
    try:
        service.status(task_id)
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=404, detail="Unknown autonomous task") from exc

    def worker() -> None:
        try:
            task = service.resume(
                task_id, clarification=payload.clarification if payload else None
            )
            payload = {"type": "agent_task_complete", "task": task.model_dump(mode="json")}
        except Exception as exc:  # noqa: BLE001
            payload = {"type": "agent_task_error", "task_id": task_id, "error": str(exc)}
        manager.send_from_worker_thread(payload)

    threading.Thread(target=worker, daemon=True).start()
    return {"task_id": task_id, "status": "resuming"}


@router.post("/{task_id}/stop")
def stop_agent_task(
    task_id: str,
    request: Request,
    user_id: int = Depends(get_current_user_id),
) -> dict:
    try:
        task = _service(request).stop(task_id)
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"task_id": task.task_id, "stop_requested": task.stop_requested}
