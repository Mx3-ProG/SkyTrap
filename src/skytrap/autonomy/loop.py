from __future__ import annotations

import json
from collections.abc import Callable

from skytrap.autonomy.executor import ToolExecutor
from skytrap.autonomy.memory import TaskStore, WorkingMemory
from skytrap.autonomy.planning import Planner, TaskPlan
from skytrap.autonomy.state import TaskState, TaskStatus
from skytrap.autonomy.verification import VerificationLoop
from skytrap.core.agent import _parse_decision
from skytrap.core.context import WorkspaceContext
from skytrap.models.base import ModelProvider


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
    ):
        self.model = model
        self.planner = planner
        self.executor = executor
        self.verifier = verifier
        self.store = store
        self.on_event = on_event
        self.should_stop = should_stop or (lambda: False)

    def run(self, workspace: WorkspaceContext, task: TaskState) -> TaskState:
        memory = WorkingMemory(objective=task.goal)
        return self._run(workspace, task, memory)

    def resume(self, workspace: WorkspaceContext, task_id: str) -> TaskState:
        task, memory = self.store.load(task_id)
        task.begin_new_run()
        return self._run(workspace, task, memory)

    def _save(self, task: TaskState, memory: WorkingMemory) -> None:
        self.store.save(task, memory)
        if self.on_event:
            self.on_event({"task": task.model_dump(mode="json")})

    def _run(self, workspace: WorkspaceContext, task: TaskState, memory: WorkingMemory) -> TaskState:
        try:
            if task.plan is None:
                task.transition(TaskStatus.PLANNING)
                self._save(task, memory)
                plan = self.planner.create_plan(workspace, task.goal)
                task.plan = plan.model_dump(mode="json")
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
                    return task

                task.iteration += 1
                raw = self.model.chat(messages)
                decision = _parse_decision(raw)
                messages.append({"role": "assistant", "content": raw})

                if decision.type == "tool_call":
                    result = self.executor.execute(
                        task, memory, workspace, decision.tool or "", decision.arguments
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
                verification = self.verifier.run(workspace)
                memory.verification_results.append(verification.model_dump(mode="json"))
                if verification.success:
                    task.final_message = decision.message or "Task completed and verified."
                    task.transition(TaskStatus.COMPLETED)
                    self._save(task, memory)
                    return task

                if not verification.results:
                    task.final_message = "No verification command could be discovered; success cannot be proven."
                    task.transition(TaskStatus.BLOCKED, error=task.final_message)
                    self._save(task, memory)
                    return task

                failure = verification.results[-1]
                memory.errors.append(failure.output)
                plan = self.planner.revise_plan(plan, failure.output)
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
        except Exception as exc:  # noqa: BLE001 - persistence is the last-resort task boundary
            task.final_message = f"Autonomous task failed: {exc}"
            task.transition(TaskStatus.FAILED, error=str(exc))
            memory.errors.append(str(exc))
        self._save(task, memory)
        return task

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
                    f"Goal:\n{task.goal}\n\nPlan:\n{plan.model_dump_json(indent=2)}\n\n"
                    f"Working memory:\n{memory.compact_context()}"
                ),
            },
        ]
