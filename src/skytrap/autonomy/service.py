from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from skytrap.autonomy.approval import ApprovalDecision, ApprovalEngine, ApprovalRequest
from skytrap.autonomy.executor import ToolExecutor
from skytrap.autonomy.git_workflow import GitWorkflow
from skytrap.autonomy.intent import HumanIntentEngine, NormalizedIntent
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
        intent_engine: HumanIntentEngine | None = None,
    ):
        self.model = model
        self.store = store or TaskStore(task_state_dir())
        self.approval_callback = approval_callback
        self.on_event = on_event
        self.intent_engine = intent_engine or HumanIntentEngine()
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
            intent_engine=self.intent_engine,
        )

    def run(self, project_path: Path, goal: str, max_iterations: int = 20) -> TaskState:
        task = self.start(project_path, goal, max_iterations)
        if task.status in {TaskStatus.BLOCKED, TaskStatus.NEEDS_CLARIFICATION}:
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
        task.transition(TaskStatus.INTERPRETING)
        intent = self.intent_engine.normalize(
            goal,
            context=self.intent_engine.context_from_memory(memory, workspace, objective=goal),
            workspace=workspace,
        )
        memory.conversation.append({"role": "user", "content": goal})
        self._apply_intent(task, memory, intent)
        if intent.clarification_required:
            task.transition(TaskStatus.NEEDS_CLARIFICATION)
            task.final_message = intent.clarification_question
            self.store.save(task, memory)
            return task
        task.transition(TaskStatus.CREATED)
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

    def resume(self, task_id: str, clarification: str | None = None) -> TaskState:
        task, memory = self.store.load(task_id)
        workspace = detect_workspace(task.workspace_path)
        answered_clarification = False
        if task.status == TaskStatus.NEEDS_CLARIFICATION:
            if not clarification:
                return task
            intent = self.intent_engine.normalize(
                clarification,
                context=self.intent_engine.context_from_memory(
                    memory, workspace, objective=task.goal
                ),
                workspace=workspace,
            )
            memory.conversation.append({"role": "user", "content": clarification})
            self._apply_intent(task, memory, intent)
            if intent.clarification_required:
                task.final_message = intent.clarification_question
                self.store.save(task, memory)
                return task
            task.begin_new_run()
            answered_clarification = True
            task.normalized_intent = intent.model_dump(mode="json")
            task.final_message = None
            if not task.task_branch:
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
        branch = self.git.ensure_task_branch(workspace, task)
        if not branch.success:
            raise RuntimeError(branch.output)
        if answered_clarification:
            self.store.save(task, memory)
            return self._loop(task_id).run(workspace, task, memory)
        return self._loop(task_id).resume(workspace, task_id)

    def _apply_intent(
        self, task: TaskState, memory: WorkingMemory, intent: NormalizedIntent
    ) -> None:
        task.normalized_intent = intent.model_dump(mode="json")
        memory.referenced_entities = list(
            dict.fromkeys([*memory.referenced_entities, *intent.referenced_entities])
        )[-50:]
        for assumption in intent.assumptions:
            if assumption not in memory.assumptions:
                memory.assumptions.append(assumption)
        if self.on_event:
            self.on_event(
                {
                    "kind": "intent_interpreted",
                    "goal": intent.interpreted_goal,
                    "confidence": intent.confidence,
                    "risk": intent.risk.value,
                }
            )
            for assumption in intent.assumptions:
                self.on_event({"kind": "working_assumption", "assumption": assumption})
            if intent.clarification_required:
                self.on_event(
                    {
                        "kind": "path_forks",
                        "question": intent.clarification_question,
                        "paths": [*intent.contradictions, *intent.ambiguities][:3],
                    }
                )

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
