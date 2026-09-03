from __future__ import annotations

from collections.abc import Callable

from skytrap.autonomy.executor import ToolExecutor
from skytrap.autonomy.intent import HumanIntentEngine, NormalizedIntent
from skytrap.autonomy.memory import TaskStore, WorkingMemory
from skytrap.autonomy.planning import Planner, TaskPlan
from skytrap.autonomy.state import TaskState, TaskStatus
from skytrap.autonomy.verification import VerificationLoop, VerificationResult, VerificationStage
from skytrap.core.agent import _parse_decision
from skytrap.core.context import WorkspaceContext
from skytrap.models.base import ModelProvider
from skytrap.tools.base import ToolResult


AGENT_LOOP_PROMPT = """You are SkyTrap's autonomous implementation agent.
Work toward the goal using the real tools listed below. Inspect before editing, prefer
targeted changes, and react to tool or verification errors. Return exactly one JSON
object per response: either
{"type":"tool_call","tool":"name","arguments":{...}}
or {"type":"final","message":"implementation complete"}.
A final response triggers independent lint/typecheck/test/build verification; it does
not by itself mark the task successful. Never claim success without using tools.
"""


class AgentLoop:
    def __init__(
        self,
        model: ModelProvider,
        planner: Planner,
        executor: ToolExecutor,
        verifier: VerificationLoop,
        store: TaskStore,
        on_event: Callable[[dict], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        completion_hook: Callable[[WorkspaceContext, TaskState, WorkingMemory], ToolResult] | None = None,
        intent_engine: HumanIntentEngine | None = None,
    ):
        self.model = model
        self.planner = planner
        self.executor = executor
        self.verifier = verifier
        self.store = store
        self.on_event = on_event
        self.should_stop = should_stop or (lambda: False)
        self.completion_hook = completion_hook
        self.intent_engine = intent_engine or HumanIntentEngine()

    def run(
        self,
        workspace: WorkspaceContext,
        task: TaskState,
        memory: WorkingMemory | None = None,
    ) -> TaskState:
        memory = memory or WorkingMemory(objective=task.goal)
        return self._run(workspace, task, memory)

    def resume(self, workspace: WorkspaceContext, task_id: str) -> TaskState:
        task, memory = self.store.load(task_id)
        task.begin_new_run()
        return self._run(workspace, task, memory)

    def _save(self, task: TaskState, memory: WorkingMemory) -> None:
        self.store.save(task, memory)
        self._emit("task_state", task=task.model_dump(mode="json"))

    def _emit(self, kind: str, **payload) -> None:
        if self.on_event:
            try:
                self.on_event({"kind": kind, **payload})
            except Exception:  # noqa: BLE001 - presentation must never break execution
                pass

    def _run(self, workspace: WorkspaceContext, task: TaskState, memory: WorkingMemory) -> TaskState:
        try:
            if task.normalized_intent is None:
                task.transition(TaskStatus.INTERPRETING)
                self._save(task, memory)
                intent = self.intent_engine.normalize(
                    task.goal,
                    context=self.intent_engine.context_from_memory(memory, workspace),
                    workspace=workspace,
                )
                task.normalized_intent = intent.model_dump(mode="json")
                self._remember_intent(memory, intent)
                self._emit_intent(intent)
                if intent.clarification_required:
                    task.transition(TaskStatus.NEEDS_CLARIFICATION)
                    task.final_message = intent.clarification_question
                    self._save(task, memory)
                    return task
            else:
                intent = NormalizedIntent.model_validate(task.normalized_intent)

            if task.plan is None:
                task.transition(TaskStatus.PLANNING)
                self._save(task, memory)
                self._emit("exploration_started", target=str(workspace.path))
                plan = self.planner.create_plan(workspace, intent)
                task.plan = plan.model_dump(mode="json")
                self._emit(
                    "plan_created",
                    steps=len(plan.steps),
                    files=len(plan.files),
                    revision=plan.revision,
                )
            else:
                plan = TaskPlan.model_validate(task.plan)

            task.transition(TaskStatus.RUNNING)
            self._save(task, memory)
            messages = self._initial_messages(task, plan, memory)

            while task.iteration < task.max_iterations:
                if self.should_stop():
                    task.transition(TaskStatus.CANCELLED)
                    task.final_message = "Task stopped cleanly. State is persisted and can be resumed."
                    self._save(task, memory)
                    self._emit("task_stopped", task_id=task.task_id)
                    return task

                task.iteration += 1
                self._emit("activity", phase="planning")
                raw = self.model.chat(messages)
                decision = _parse_decision(raw)
                messages.append({"role": "assistant", "content": raw})

                if decision.type == "tool_call":
                    self._emit(
                        "activity",
                        phase="tool_call",
                        tool=decision.tool,
                        arguments=decision.arguments,
                    )
                    result = self.executor.execute(
                        task, memory, workspace, decision.tool or "", decision.arguments
                    )
                    display_arguments = {
                        key: value
                        for key, value in decision.arguments.items()
                        if key not in {"content", "replacement", "expected"}
                    }
                    self._emit(
                        "tool_result",
                        tool=decision.tool,
                        arguments=display_arguments,
                        success=result.success,
                        status=result.status,
                        output=result.output,
                        metadata=result.metadata,
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": f"Tool result for {decision.tool}:\n{result.model_dump_json()}",
                        }
                    )
                    if result.status == "needs_approval":
                        task.transition(TaskStatus.NEEDS_APPROVAL)
                        self._save(task, memory)
                        return task
                    self._save(task, memory)
                    continue

                if not any(event.kind == "tool_result" for event in memory.events):
                    messages.append(
                        {
                            "role": "user",
                            "content": "Completion rejected: inspect or act with a real tool before finishing.",
                        }
                    )
                    self._save(task, memory)
                    continue

                task.transition(TaskStatus.VERIFYING)
                self._emit("verification_started")
                verification = self._verify(workspace, task, memory)
                memory.verification_results.append(verification.model_dump(mode="json"))
                if verification.success:
                    if self.completion_hook is not None:
                        self._emit("activity", phase="checkpoint")
                        checkpoint = self.completion_hook(workspace, task, memory)
                        self._emit(
                            "checkpoint",
                            success=checkpoint.success,
                            output=checkpoint.output,
                            metadata=checkpoint.metadata,
                        )
                        memory.record(
                            "checkpoint",
                            success=checkpoint.success,
                            error=checkpoint.stderr or checkpoint.output if not checkpoint.success else None,
                        )
                        if not checkpoint.success:
                            task.final_message = f"Verification passed but checkpoint failed: {checkpoint.output}"
                            task.transition(TaskStatus.BLOCKED, error=task.final_message)
                            self._save(task, memory)
                            self._emit("task_error", error=task.final_message)
                            return task
                    task.final_message = decision.message or "Task completed and verified."
                    task.transition(TaskStatus.COMPLETED)
                    self._save(task, memory)
                    self._emit("task_completed", task=task.model_dump(mode="json"))
                    return task

                if not verification.results:
                    task.final_message = "No verification command could be discovered; success cannot be proven."
                    task.transition(TaskStatus.BLOCKED, error=task.final_message)
                    self._save(task, memory)
                    self._emit("task_error", error=task.final_message)
                    return task

                failure = verification.results[-1]
                memory.errors.append(failure.output)
                plan = self.planner.revise_plan(plan, failure.output)
                self._emit(
                    "retry",
                    stage=verification.failed_stage.value if verification.failed_stage else None,
                    revision=plan.revision,
                    error=failure.output,
                )
                task.plan = plan.model_dump(mode="json")
                task.transition(TaskStatus.RUNNING)
                messages.append(
                    {
                        "role": "user",
                        "content": "Verification failed. Diagnose, fix, then finish again.\n" + failure.model_dump_json(),
                    }
                )
                self._save(task, memory)

            task.final_message = f"Iteration limit reached ({task.max_iterations})."
            task.transition(TaskStatus.FAILED, error=task.final_message)
            self._emit("task_error", error=task.final_message)
        except Exception as exc:  # noqa: BLE001 - persistence is the last-resort task boundary
            task.final_message = f"Autonomous task failed: {exc}"
            task.transition(TaskStatus.FAILED, error=str(exc))
            memory.errors.append(str(exc))
            self._emit("task_error", error=str(exc))
        self._save(task, memory)
        return task

    def _remember_intent(self, memory: WorkingMemory, intent: NormalizedIntent) -> None:
        memory.referenced_entities = list(
            dict.fromkeys([*memory.referenced_entities, *intent.referenced_entities])
        )[-50:]
        for assumption in intent.assumptions:
            if assumption not in memory.assumptions:
                memory.assumptions.append(assumption)

    def _emit_intent(self, intent: NormalizedIntent) -> None:
        self._emit(
            "intent_interpreted",
            goal=intent.interpreted_goal,
            confidence=intent.confidence,
            risk=intent.risk.value,
        )
        for assumption in intent.assumptions:
            self._emit("working_assumption", assumption=assumption)
        if intent.clarification_required:
            self._emit(
                "path_forks",
                question=intent.clarification_question,
                paths=[*intent.contradictions, *intent.ambiguities][:3],
            )

    def _verify(
        self,
        workspace: WorkspaceContext,
        task: TaskState,
        memory: WorkingMemory,
    ) -> VerificationResult:
        tool_names = {
            VerificationStage.LINT: "lint",
            VerificationStage.TYPECHECK: "typecheck",
            VerificationStage.TEST: "run_tests",
            VerificationStage.BUILD: "build",
        }
        if not all(name in self.executor.tools for name in tool_names.values()):
            return self.verifier.run(workspace)

        results: list[ToolResult] = []
        skipped: list[VerificationStage] = []
        for stage, tool_name in tool_names.items():
            self._emit("activity", phase="verification", stage=stage.value)
            result = self.executor.execute(task, memory, workspace, tool_name, {})
            self._emit(
                "verification_stage",
                stage=stage.value,
                success=result.success,
                skipped=bool(result.metadata.get("skipped")),
                output=result.output,
            )
            if result.metadata.get("skipped"):
                skipped.append(stage)
                continue
            results.append(result)
            if not result.success:
                return VerificationResult(
                    success=False,
                    results=results,
                    failed_stage=stage,
                    skipped_stages=skipped,
                )
        return VerificationResult(
            success=bool(results),
            results=results,
            skipped_stages=skipped,
        )

    def _initial_messages(
        self, task: TaskState, plan: TaskPlan, memory: WorkingMemory
    ) -> list[dict]:
        tool_descriptions = "\n".join(
            f"- {tool.name}: {tool.description}" for tool in self.executor.tools.values()
        )
        return [
            {
                "role": "system",
                "content": AGENT_LOOP_PROMPT + "\nAvailable tools:\n" + tool_descriptions,
            },
            {
                "role": "user",
                "content": (
                    "Normalized intent:\n"
                    f"{NormalizedIntent.model_validate(task.normalized_intent).model_dump_json(indent=2)}"
                    f"\n\nPlan:\n{plan.model_dump_json(indent=2)}\n\n"
                    f"Working memory:\n{memory.compact_context()}"
                ),
            },
        ]
