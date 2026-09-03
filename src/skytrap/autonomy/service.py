from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from pathlib import Path

from skytrap.autonomy.approval import ApprovalDecision, ApprovalEngine, ApprovalRequest
from skytrap.autonomy.executor import ToolExecutor
from skytrap.autonomy.git_workflow import GitWorkflow
from skytrap.autonomy.intent import HumanIntentEngine, NormalizedIntent
from skytrap.autonomy.loop import AgentLoop
from skytrap.autonomy.memory import TaskStore, WorkingMemory
from skytrap.autonomy.planning import Planner
from skytrap.autonomy.review import IndependentReviewer
from skytrap.autonomy.risk import FULL_INTERACTIVE_CAPABILITIES, RiskEngine, RiskLevel
from skytrap.autonomy.state import TaskState, TaskStatus
from skytrap.autonomy.tools import build_autonomous_tools
from skytrap.autonomy.verification import VerificationLoop
from skytrap.core.context import WorkspaceContext, detect_workspace
from skytrap.intelligence.repository_memory import RepositoryMemory, RepositoryMemoryStore
from skytrap.intelligence.snapshot import build_repository_snapshot
from skytrap.memory.sqlite import DEFAULT_DB_PATH
from skytrap.models.base import ModelProvider
from skytrap.models.base import ModelRole
from skytrap.models.router import ModelRouter


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
        model_router: ModelRouter | None = None,
    ):
        self.model = model
        self.store = store or TaskStore(task_state_dir())
        self.approval_callback = approval_callback
        self.on_event = on_event
        self.intent_engine = intent_engine or HumanIntentEngine()
        self.model_router = model_router or ModelRouter([model])
        self.git = GitWorkflow()

    def _loop(self, task_id: str) -> AgentLoop:
        coding_model = self._routed_model(ModelRole.CODING)
        planning_model = self._routed_model(ModelRole.REASONING)
        review_model = self._routed_model(ModelRole.REVIEW)
        if review_model.name == coding_model.name:
            review_model = None
        verifier = VerificationLoop()
        tools = build_autonomous_tools(verifier)
        executor = ToolExecutor(
            tools,
            RiskEngine(),
            ApprovalEngine(
                callback=self.approval_callback,
                auto_approve_through=RiskLevel.MEDIUM,
            ),
            capabilities=FULL_INTERACTIVE_CAPABILITIES,
        )
        return AgentLoop(
            coding_model,
            Planner(planning_model),
            executor,
            verifier,
            self.store,
            on_event=self.on_event,
            should_stop=lambda: self.store.is_stop_requested(task_id),
            completion_hook=self.git.checkpoint,
            intent_engine=self.intent_engine,
            reviewer=IndependentReviewer(review_model),
            repository_memory_store=self._repository_memory_store(),
        )

    @staticmethod
    def _repository_memory_store() -> RepositoryMemoryStore | None:
        """Item 3 — best-effort: a repository memory store that fails to open must
        never block a task from running read/write, just from getting the
        discovery speed-up."""
        try:
            DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            return RepositoryMemoryStore(sqlite3.connect(DEFAULT_DB_PATH))
        except Exception:  # noqa: BLE001
            return None

    def _routed_model(self, role: ModelRole) -> ModelProvider:
        try:
            return self.model_router.route(role)
        except LookupError:
            return self.model

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
        intent_context = self.intent_engine.context_from_memory(
            memory, workspace, objective=goal
        )
        memory.conversation.append({"role": "user", "content": goal})
        task.transition(TaskStatus.INTERPRETING)
        self.store.save(task, memory)
        intent = self.intent_engine.normalize(
            goal,
            context=intent_context,
            workspace=workspace,
        )
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
        loop = self._loop(task_id)
        completed = loop.run(workspace, task, memory)
        if completed.status == TaskStatus.COMPLETED:
            self._persist_repository_memory(workspace, memory, loop.last_symbol_index)
        return completed

    def _persist_repository_memory(
        self, workspace: WorkspaceContext, memory: WorkingMemory, symbol_index=None
    ) -> None:
        """Item 3/14 — a compact, fingerprinted summary of this repository (now
        including its parsed symbols — see `RepositoryMemory.parsed_files`) is
        kept so a future fingerprint-matching task can skip re-parsing the whole
        repository. Best-effort: a failure here must never fail an otherwise-
        completed task."""
        try:
            snapshot = build_repository_snapshot(workspace)
            DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(DEFAULT_DB_PATH)
            try:
                RepositoryMemoryStore(connection).save(
                    RepositoryMemory.from_snapshot(
                        snapshot, decisions=memory.decisions[-20:], symbol_index=symbol_index
                    )
                )
            finally:
                connection.close()
        except Exception:  # noqa: BLE001 - repository memory is best-effort
            pass

    def resume(self, task_id: str, clarification: str | None = None) -> TaskState:
        task, memory = self.store.load(task_id)
        workspace = detect_workspace(task.workspace_path)
        answered_clarification = False
        if task.status == TaskStatus.NEEDS_CLARIFICATION:
            if not clarification:
                return task
            memory.revise_assumptions("Superseded by the user's clarification")
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
            memory.record_assumption(assumption)
        self._emit(
            {
                "kind": "intent_interpreted",
                "goal": intent.interpreted_goal,
                "confidence": intent.confidence,
                "risk": intent.risk.value,
            }
        )
        for assumption in intent.assumptions:
            self._emit({"kind": "working_assumption", "assumption": assumption})
        if intent.clarification_required:
            self._emit(
                {
                    "kind": "path_forks",
                    "question": intent.clarification_question,
                    "paths": [*intent.contradictions, *intent.ambiguities][:3],
                }
            )

    def _emit(self, event: dict) -> None:
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:  # noqa: BLE001 - presentation must not block task creation
                pass

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
