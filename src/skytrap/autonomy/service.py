from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from skytrap.autonomy.approval import ApprovalDecision, ApprovalEngine, ApprovalRequest
from skytrap.autonomy.executor import ToolExecutor
from skytrap.autonomy.git_workflow import GitWorkflow
from skytrap.autonomy.loop import AgentLoop
from skytrap.autonomy.memory import TaskStore, WorkingMemory
from skytrap.autonomy.planning import Planner
from skytrap.autonomy.risk import Capability, RiskEngine, RiskLevel
from skytrap.autonomy.state import TaskState, TaskStatus
from skytrap.autonomy.tools import build_autonomous_tools
from skytrap.autonomy.verification import VerificationLoop
from skytrap.core.context import WorkspaceContext, detect_workspace
from skytrap.models.base import ModelProvider


DEFAULT_STATE_DIR = Path.home() / ".skytrap" / "tasks"


def task_state_dir() -> Path:
    configured = os.environ.get("SKYTRAP_STATE_DIR")
    return Path(configured).expanduser().resolve() if configured else DEFAULT_STATE_DIR


class AutonomousTaskService:
    """Application service shared by the `skytrap agent` CLI commands."""

    def __init__(
        self,
        model: ModelProvider,
        store: TaskStore | None = None,
        approval_callback: Callable[[ApprovalRequest], ApprovalDecision | bool | None] | None = None,
        on_event: Callable[[dict], None] | None = None,
    ):
        self.model = model
        self.store = store or TaskStore(task_state_dir())
        self.approval_callback = approval_callback
        self.on_event = on_event
        self.git = GitWorkflow()

    def _loop(self, task_id: str) -> AgentLoop:
        verifier = VerificationLoop()
        tools = build_autonomous_tools(verifier)
        executor = ToolExecutor(
            tools,
            RiskEngine(),
            ApprovalEngine(
                callback=self.approval_callback,
                auto_approve_through=RiskLevel.MEDIUM,
            ),
            capabilities={
                Capability.FILESYSTEM_READ,
                Capability.FILESYSTEM_WRITE,
                Capability.SHELL_EXECUTE,
            },
        )
        return AgentLoop(
            self.model,
            Planner(self.model),
            executor,
            verifier,
            self.store,
            on_event=self.on_event,
            should_stop=lambda: self.store.is_stop_requested(task_id),
            completion_hook=self.git.checkpoint,
        )

    def run(self, project_path: Path, goal: str, max_iterations: int = 20) -> TaskState:
        task = self.start(project_path, goal, max_iterations)
        if task.status == TaskStatus.BLOCKED:
            return task
        return self.execute(task.task_id)

    def start(self, project_path: Path, goal: str, max_iterations: int = 20) -> TaskState:
        """Create, persist, and prepare a task without starting model execution."""
        workspace = detect_workspace(project_path.expanduser().resolve())
        task = TaskState(
            workspace_path=workspace.path,
            project_id=str(workspace.path),
            goal=goal,
            max_iterations=max_iterations,
        )
        memory = WorkingMemory(objective=goal)
        self.store.save(task, memory)
        prepared = self.git.prepare(workspace, task)
        if not prepared.success:
            task.transition(TaskStatus.BLOCKED, error=prepared.output)
            task.final_message = prepared.output
            self.store.save(task, memory)
            return task
        memory.git_state = (
            f"branch {task.task_branch} from {task.base_commit}; original {task.original_branch}"
        )
        self.store.save(task, memory)
        return task

    def execute(self, task_id: str) -> TaskState:
        """Execute a task created with start(); useful for background server workers."""
        task, memory = self.store.load(task_id)
        workspace = detect_workspace(task.workspace_path)
        branch = self.git.ensure_task_branch(workspace, task)
        if not branch.success:
            task.transition(TaskStatus.BLOCKED, error=branch.output)
            task.final_message = branch.output
            self.store.save(task, memory)
            return task
        return self._loop(task_id).run(workspace, task, memory)

    def resume(self, task_id: str) -> TaskState:
        task, _ = self.store.load(task_id)
        workspace = detect_workspace(task.workspace_path)
        branch = self.git.ensure_task_branch(workspace, task)
        if not branch.success:
            raise RuntimeError(branch.output)
        return self._loop(task_id).resume(workspace, task_id)

    def status(self, task_id: str) -> TaskState:
        task, _ = self.store.load(task_id)
        return task

    def list_tasks(self) -> list[TaskState]:
        return self.store.list_tasks()

    def stop(self, task_id: str) -> TaskState:
        return self.store.request_stop(task_id)

    def rollback(self, task_id: str) -> TaskState:
        task, memory = self.store.load(task_id)
        workspace = detect_workspace(task.workspace_path)
        result = self.git.rollback(workspace, task, memory)
        if not result.success:
            raise RuntimeError(result.output)
        self.store.save(task, memory)
        return task
