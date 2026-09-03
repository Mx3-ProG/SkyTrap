from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from skytrap.autonomy.state import TaskState, TaskStatus


class MemoryEvent(BaseModel):
    kind: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class WorkingMemory(BaseModel):
    """Bounded, serializable evidence accumulated during an autonomous task."""

    objective: str
    conversation: list[dict[str, str]] = Field(default_factory=list)
    referenced_entities: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    files_consulted: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)
    commands_executed: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    corrections: list[str] = Field(default_factory=list)
    git_state: str | None = None
    verification_results: list[dict[str, Any]] = Field(default_factory=list)
    events: list[MemoryEvent] = Field(default_factory=list)

    def record(self, kind: str, **data: Any) -> None:
        self.events.append(MemoryEvent(kind=kind, data=data))
        path = data.get("path")
        if kind == "tool_result" and path and data.get("tool") == "read_file":
            if path not in self.files_consulted:
                self.files_consulted.append(path)
        if kind == "tool_result" and path and data.get("tool") in {"write_file", "patch_file", "delete_file"}:
            if path not in self.files_modified:
                self.files_modified.append(path)
        command = data.get("command")
        if command and command not in self.commands_executed:
            self.commands_executed.append(command)
        if data.get("success") is False and data.get("error"):
            self.errors.append(str(data["error"]))

    def compact_context(self, max_events: int = 20) -> str:
        payload = {
            "objective": self.objective,
            "conversation": self.conversation[-12:],
            "referenced_entities": self.referenced_entities[-20:],
            "assumptions": self.assumptions[-20:],
            "decisions": self.decisions[-20:],
            "files_consulted": self.files_consulted[-30:],
            "files_modified": self.files_modified[-30:],
            "commands_executed": self.commands_executed[-20:],
            "errors": self.errors[-10:],
            "events": [event.model_dump(mode="json") for event in self.events[-max_events:]],
        }
        return json.dumps(payload, ensure_ascii=False)


class TaskStore:
    """Atomic JSON persistence suitable for local runs and straightforward handoff."""

    def __init__(self, root: Path):
        self.root = root

    def _path(self, task_id: str) -> Path:
        if not task_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in task_id):
            raise ValueError("Invalid task id")
        return self.root / f"{task_id}.json"

    def save(self, task: TaskState, memory: WorkingMemory) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(task.task_id)
        if path.exists() and task.status != TaskStatus.CREATED and not task.stop_requested:
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                task.stop_requested = bool(existing["task"].get("stop_requested"))
            except (OSError, ValueError, KeyError):
                pass
        temporary = self.root / f".{task.task_id}.{uuid4().hex}.tmp"
        payload = {
            "version": 1,
            "task": task.model_dump(mode="json"),
            "memory": memory.model_dump(mode="json"),
        }
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def load(self, task_id: str) -> tuple[TaskState, WorkingMemory]:
        data = json.loads(self._path(task_id).read_text(encoding="utf-8"))
        if data.get("version") != 1:
            raise ValueError(f"Unsupported task state version: {data.get('version')}")
        return TaskState.model_validate(data["task"]), WorkingMemory.model_validate(data["memory"])

    def list_tasks(self) -> list[TaskState]:
        if not self.root.is_dir():
            return []
        tasks: list[TaskState] = []
        for path in sorted(self.root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                tasks.append(TaskState.model_validate(data["task"]))
            except (OSError, ValueError, KeyError):
                continue
        return tasks

    def request_stop(self, task_id: str) -> TaskState:
        task, memory = self.load(task_id)
        if task.is_terminal:
            raise ValueError(f"Task is already {task.status.value}")
        task.stop_requested = True
        self.save(task, memory)
        return task

    def is_stop_requested(self, task_id: str) -> bool:
        try:
            task, _ = self.load(task_id)
        except (OSError, ValueError, KeyError):
            return False
        return task.stop_requested
