from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TaskStatus(StrEnum):
    CREATED = "created"
    PLANNING = "planning"
    RUNNING = "running"
    VERIFYING = "verifying"
    NEEDS_APPROVAL = "needs_approval"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = {
    TaskStatus.COMPLETED,
    TaskStatus.BLOCKED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
}


class TaskState(BaseModel):
    """Durable lifecycle state for one autonomous task."""

    task_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    project_id: str | None = None
    machine_id: str | None = None
    workspace_path: Path
    goal: str
    status: TaskStatus = TaskStatus.CREATED
    iteration: int = 0
    max_iterations: int = 20
    plan: dict[str, Any] | None = None
    final_message: str | None = None
    error: str | None = None
    pending_approval: dict[str, Any] | None = None
    stop_requested: bool = False
    original_branch: str | None = None
    task_branch: str | None = None
    base_commit: str | None = None
    checkpoint_commit: str | None = None
    final_diff: str | None = None
    rolled_back: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def transition(self, status: TaskStatus, *, error: str | None = None) -> None:
        if self.is_terminal and status != self.status:
            raise ValueError(f"Cannot transition terminal task from {self.status} to {status}")
        self.status = status
        self.error = error
        self.updated_at = utc_now()

    def begin_new_run(self) -> None:
        if self.status == TaskStatus.COMPLETED:
            raise ValueError("A completed task cannot be resumed")
        self.run_id = uuid4().hex
        self.iteration = 0
        self.status = TaskStatus.CREATED
        self.pending_approval = None
        self.stop_requested = False
        self.updated_at = utc_now()
