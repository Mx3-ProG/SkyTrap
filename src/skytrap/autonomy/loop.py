from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable

from skytrap.autonomy.executor import ToolExecutor
from skytrap.autonomy.browser_verification import BrowserVerificationProvider
from skytrap.autonomy.evidence import ExecutionEvidence
from skytrap.autonomy.intent import HumanIntentEngine, NormalizedIntent
from skytrap.autonomy.memory import TaskStore, WorkingMemory
from skytrap.autonomy.planning import Planner, TaskPlan
from skytrap.autonomy.review import IndependentReviewer
from skytrap.autonomy.state import TaskState, TaskStatus
from skytrap.autonomy.verification import VerificationLoop, VerificationResult, VerificationStage
from skytrap.core.protocol import _parse_decision
from skytrap.core.context import WorkspaceContext
from skytrap.intelligence.context_builder import ContextBuilder
from skytrap.intelligence.existence import ExistenceEvidence, check_existence
from skytrap.intelligence.graph import DependencyGraph
from skytrap.intelligence.lsp import LanguageIntelligenceProvider
from skytrap.intelligence.policy import policy_prompt
from skytrap.intelligence.repository_memory import RepositoryMemory, RepositoryMemoryStore
from skytrap.intelligence.snapshot import RepositorySnapshot, build_repository_snapshot
from skytrap.intelligence.symbols import SymbolIndex
from skytrap.models.base import ModelProvider
from skytrap.tools.base import ToolResult


AGENT_LOOP_PROMPT = """You are SkyTrap's autonomous implementation agent.
Work toward the goal using the real tools listed below. The evidence below (repository
architecture, existing-evidence bullets, relevant files/symbols) was gathered by real
inspection before you were asked to act — READ -> UNDERSTAND -> PROVE -> PLAN -> PATCH ->
VERIFY, never PROMPT -> GENERATE FILES. Never call write_file to "create" a path the
evidence says already exists — read it and modify it instead. If evidence for something
is UNKNOWN (inspection was inconclusive), investigate further (read_file/search_code)
before deciding it's missing.
Inspect before editing, prefer targeted changes, and react to tool or verification
errors. Return exactly one JSON object per response: either
{"type":"tool_call","tool":"name","arguments":{...}}
or {"type":"final","message":"implementation complete"}.
A final response triggers independent lint/typecheck/test/build verification; it does
not by itself mark the task successful. Never claim success without using tools — your
final message is cross-checked against the tools you actually called.
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
        reviewer: IndependentReviewer | None = None,
        browser_verifier: BrowserVerificationProvider | None = None,
        repository_memory_store: RepositoryMemoryStore | None = None,
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
        self.reviewer = reviewer or IndependentReviewer()
        self.browser_verifier = browser_verifier or BrowserVerificationProvider()
        # Item 3 — CONSUME REPOSITORY MEMORY. Optional: when given, a
        # fingerprint-matching prior run's parsed symbols are reused instead of
        # re-parsing the whole repository. `last_symbol_index`/`last_repository_
        # memory_metrics` are populated by `_run` for the caller (see
        # AutonomousTaskService) to persist an updated RepositoryMemory afterward.
        self.repository_memory_store = repository_memory_store
        self.last_symbol_index: SymbolIndex | None = None
        self.last_repository_memory_metrics: dict = {}

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

            # Item 1/15 — RepositoryDiscovery: real, checkable evidence about what
            # already exists, built once and handed to the planner as "Existing
            # evidence" and to the agent loop as its working context. A bare file
            # tree is never treated as sufficient understanding on its own.
            self._emit("activity", phase="verification", stage="repository discovery")
            # The file listing/fingerprint is ALWAYS recomputed fresh — the current
            # repository state is the source of truth, never the cache. Only the
            # expensive derived intelligence (parsed symbols) is ever reused, and
            # only when the fingerprint proves nothing has changed since it was
            # computed.
            snapshot = build_repository_snapshot(workspace)
            symbol_index, memory_metrics = self._load_or_build_symbol_index(workspace, snapshot)
            self.last_symbol_index = symbol_index
            self.last_repository_memory_metrics = memory_metrics
            self._emit("repository_memory", **memory_metrics)
            dependency_graph = DependencyGraph().build(symbol_index, snapshot.files)
            self.executor.symbol_index = symbol_index
            existence_evidence = [
                check_existence(workspace, snapshot, entity, symbol_hint=symbol_index.all_names())
                for entity in intent.referenced_entities[:8]
            ]
            lsp_evidence = self._collect_lsp_evidence(
                workspace, snapshot, existence_evidence
            )
            self._emit(
                "repository_discovery",
                files=len(snapshot.files),
                truncated=snapshot.truncated,
                languages=snapshot.languages,
                frameworks=snapshot.frameworks,
                entrypoints=snapshot.entrypoints,
                evidence=[item.as_bullet() for item in existence_evidence],
                lsp=lsp_evidence,
            )

            if task.plan is None:
                task.transition(TaskStatus.PLANNING)
                self._save(task, memory)
                self._emit("exploration_started", target=str(workspace.path))
                plan = self.planner.create_plan(
                    workspace,
                    intent,
                    snapshot=snapshot,
                    existence_evidence=existence_evidence,
                    symbol_index=symbol_index,
                )
                task.plan = plan.model_dump(mode="json")
                self._emit(
                    "plan_created",
                    steps=len(plan.steps),
                    files=len(plan.files),
                    revision=plan.revision,
                    file_actions=[action.model_dump(mode="json") for action in plan.file_actions],
                )
            else:
                plan = TaskPlan.model_validate(task.plan)

            task.transition(TaskStatus.RUNNING)
            self._save(task, memory)
            messages = self._initial_messages(
                workspace, task, plan, memory, snapshot, symbol_index, dependency_graph,
                existence_evidence, lsp_evidence
            )

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
                    browser_failure = self._verify_browser_if_applicable(
                        workspace, snapshot, memory
                    )
                    if browser_failure:
                        plan = self.planner.revise_plan(plan, browser_failure)
                        task.plan = plan.model_dump(mode="json")
                        task.transition(TaskStatus.RUNNING)
                        messages.append(
                            {"role": "user", "content": "Browser verification failed. Repair the application behavior, then verify again.\n" + browser_failure}
                        )
                        self._emit("retry", stage="browser", revision=plan.revision, error=browser_failure)
                        self._save(task, memory)
                        continue

                    task.transition(TaskStatus.REVIEWING)
                    diff = self._working_diff(workspace)
                    evidence = ExecutionEvidence.from_memory(memory, success_criteria=plan.success_criteria)
                    review = self.reviewer.review(
                        original_request=task.goal,
                        intent=intent,
                        snapshot=snapshot,
                        diff=diff,
                        verification_results=memory.verification_results,
                        diagnostics=memory.errors,
                        evidence=evidence,
                    )
                    memory.review_results.append(review.model_dump(mode="json"))
                    task.review_result = review.model_dump(mode="json")
                    self._emit("review_completed", passed=review.passed, findings=[item.model_dump(mode="json") for item in review.findings], reviewer=review.reviewer)
                    if not review.passed:
                        failure_text = "\n".join(item.detail for item in review.findings if item.severity.value == "serious")
                        plan = self.planner.revise_plan(plan, failure_text)
                        task.plan = plan.model_dump(mode="json")
                        task.transition(TaskStatus.RUNNING)
                        messages.append({"role": "user", "content": "Independent review failed. Repair these findings, without weakening tests:\n" + failure_text})
                        self._emit("retry", stage="review", revision=plan.revision, error=failure_text)
                        self._save(task, memory)
                        continue

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
                            output=checkpoint.output,
                            **checkpoint.metadata,
                            error=checkpoint.stderr or checkpoint.output if not checkpoint.success else None,
                        )
                        if not checkpoint.success:
                            task.final_message = f"Verification passed but checkpoint failed: {checkpoint.output}"
                            task.transition(TaskStatus.BLOCKED, error=task.final_message)
                            self._save(task, memory)
                            self._emit("task_error", error=task.final_message)
                            return task
                    final_evidence = ExecutionEvidence.from_memory(memory, success_criteria=plan.success_criteria)
                    task.execution_evidence = final_evidence.model_dump(mode="json")
                    task.final_message = _claim_validated_final_message(
                        decision.message, memory, evidence=final_evidence
                    )
                    task.transition(TaskStatus.COMPLETED)
                    self._save(task, memory)
                    self._emit("task_completed", task=task.model_dump(mode="json"), verified=_verified_summary(memory))
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

    def _verify_browser_if_applicable(
        self, workspace: WorkspaceContext, snapshot: RepositorySnapshot, memory: WorkingMemory
    ) -> str | None:
        if not set(snapshot.frameworks) & {"React", "Next.js", "Vue", "Nuxt", "Svelte", "SvelteKit"}:
            return None
        self._emit("activity", phase="verification", stage="browser")
        result = self.browser_verifier.verify(workspace, snapshot)
        memory.record("browser_check", **result.model_dump(mode="json"))
        self._emit("browser_verification", **result.model_dump(mode="json"))
        if result.skipped:
            return None
        return None if result.success else result.detail or "Browser verification failed"

    def _load_or_build_symbol_index(
        self, workspace: WorkspaceContext, snapshot: RepositorySnapshot
    ) -> tuple[SymbolIndex, dict]:
        """Item 3 — CONSUME REPOSITORY MEMORY. Tries a fingerprint-matching cache
        hit first; falls back to a full Tree-sitter rebuild on any miss
        (never-cached, or the repository changed since — "invalidated"). The
        current repository is always the source of truth: a mismatched
        fingerprint is never trusted, only ever discarded and recomputed."""
        started = time.monotonic()
        if self.repository_memory_store is not None:
            cached = self.repository_memory_store.load_if_current(str(workspace.path), snapshot.fingerprint)
            existing = self.repository_memory_store.load(str(workspace.path))
            if cached is not None and cached.parsed_files:
                index = SymbolIndex.restore_from(cached.parsed_files)
                elapsed_full_estimate = self._estimate_full_parse_ms(snapshot)
                return index, {
                    "memory_hit": True,
                    "memory_miss": False,
                    "memory_invalidated": False,
                    "discovery_time_saved_ms": max(0.0, elapsed_full_estimate - (time.monotonic() - started) * 1000),
                }
            invalidated = existing is not None and existing.fingerprint != snapshot.fingerprint
            index = SymbolIndex().build(workspace)
            return index, {
                "memory_hit": False,
                "memory_miss": not invalidated,
                "memory_invalidated": invalidated,
                "discovery_time_saved_ms": 0.0,
            }
        index = SymbolIndex().build(workspace)
        return index, {
            "memory_hit": False,
            "memory_miss": True,
            "memory_invalidated": False,
            "discovery_time_saved_ms": 0.0,
        }

    @staticmethod
    def _estimate_full_parse_ms(snapshot: RepositorySnapshot) -> float:
        """A restored index skips parsing entirely, so there's nothing left to time
        against for "time saved" — this is a deliberately simple, honestly-labeled
        estimate (a fixed per-file cost), not a measured benchmark."""
        return len(snapshot.files) * 2.0

    @staticmethod
    def _collect_lsp_evidence(
        workspace: WorkspaceContext,
        snapshot: RepositorySnapshot,
        existence_evidence: list[ExistenceEvidence],
    ) -> list[str]:
        provider = LanguageIntelligenceProvider()
        candidates = []
        for item in existence_evidence:
            candidates.extend(item.matched_files)
        candidates.extend(snapshot.entrypoints)
        evidence = []
        for path in list(dict.fromkeys(candidates))[:3]:
            language = provider._language_for(path)
            if not language or not provider.is_available(language):
                continue
            result = provider.document_symbols(workspace, path, language=language)
            if result.supported:
                encoded = str(result.data)
                evidence.append(f"{path}: {encoded[:1200]}")
        return evidence

    @staticmethod
    def _working_diff(workspace: WorkspaceContext) -> str:
        if not workspace.is_git:
            return ""
        try:
            result = subprocess.run(
                ["git", "diff", "--binary", "--"],
                cwd=workspace.path,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return result.stdout if result.returncode == 0 else ""

    def _remember_intent(self, memory: WorkingMemory, intent: NormalizedIntent) -> None:
        memory.referenced_entities = list(
            dict.fromkeys([*memory.referenced_entities, *intent.referenced_entities])
        )[-50:]
        for assumption in intent.assumptions:
            memory.record_assumption(assumption)

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
        self,
        workspace: WorkspaceContext,
        task: TaskState,
        plan: TaskPlan,
        memory: WorkingMemory,
        snapshot: RepositorySnapshot,
        symbol_index: SymbolIndex,
        dependency_graph: DependencyGraph,
        existence_evidence: list[ExistenceEvidence],
        lsp_evidence: list[str],
    ) -> list[dict]:
        tool_descriptions = "\n".join(
            f"- {tool.name}: {tool.description}" for tool in self.executor.tools.values()
        )
        intent = NormalizedIntent.model_validate(task.normalized_intent)
        # Item 9 — ContextBuilder: a real, prioritized, token-budgeted context
        # instead of goal + a raw repo map. Lower-priority sections are dropped
        # first when the budget is tight; the whole repository is never injected.
        built_context = ContextBuilder().build(
            workspace,
            goal=intent.interpreted_goal,
            snapshot=snapshot,
            symbol_index=symbol_index,
            dependency_graph=dependency_graph,
            existence_evidence=existence_evidence,
            recent_decisions=memory.decisions[-10:],
            constraints=intent.implicit_constraints,
            diagnostics=lsp_evidence,
            previous_errors=memory.errors,
        )
        return [
            {
                "role": "system",
                "content": (
                    AGENT_LOOP_PROMPT
                    + "\n"
                    + policy_prompt(snapshot.languages)
                    + "\nAvailable tools:\n"
                    + tool_descriptions
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Normalized intent:\n{intent.model_dump_json(indent=2)}"
                    f"\n\nPlan:\n{plan.model_dump_json(indent=2)}\n\n"
                    f"Context:\n{built_context.render()}\n\n"
                    f"Working memory:\n{memory.compact_context()}"
                ),
            },
        ]


# Item 16 — AGENT CLAIM VALIDATION. The model must never be able to announce
# "I created X" / "I fixed X" purely because it believes it did — the final
# report is built from the *real* tool_result events the executor recorded,
# not from the model's free-text message. If the model's own message claims
# to have "created" something that the events show was actually only
# modified (i.e. it already existed), that claim is called out explicitly
# rather than passed through.
_CLAIM_CREATED = re.compile(
    r"\b(?:created?|creat[ée]e?|cr[ée][ée]?)\b[^.\n]{0,40}?([\w./-]+\.[A-Za-z0-9]+)", re.IGNORECASE
)


def _verified_summary(memory: WorkingMemory) -> dict:
    created: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    for event in memory.events:
        if event.kind != "tool_result" or not event.data.get("success"):
            continue
        tool = event.data.get("tool")
        path = event.data.get("path")
        if not path or tool not in {"write_file", "patch_file", "delete_file"}:
            continue
        if tool == "delete_file" or event.data.get("is_delete"):
            if path not in deleted:
                deleted.append(path)
        elif event.data.get("is_new_file"):
            if path not in created:
                created.append(path)
        elif path not in modified:
            modified.append(path)
    commands_run = list(memory.commands_executed)
    tests_run = [c for c in commands_run if "test" in c.lower() or "pytest" in c.lower()]
    return {
        "created_files": created,
        "modified_files": modified,
        "deleted_files": deleted,
        "commands_run": commands_run,
        "tests_run": tests_run,
    }


def _claim_validated_final_message(
    decision_message: str | None,
    memory: WorkingMemory,
    evidence: ExecutionEvidence | None = None,
) -> str:
    """Item 7 — STRUCTURED EXECUTION EVIDENCE. The renderer is not allowed to
    freely repeat the model's own claims: the deterministic evidence report
    (built purely from real tool_result/verification/review/browser_check
    events — see `ExecutionEvidence.render_structured_report`) is the primary,
    load-bearing content. The model's own message is kept underneath, clearly
    labeled as unverified, for readability/context only — never as the source
    of truth. Any "created X" claim contradicted by the evidence (X was
    actually only modified) is called out explicitly on top of that."""
    evidence = evidence or ExecutionEvidence.from_memory(memory)
    message = decision_message or "(model gave no final summary)"

    corrections = []
    for match in _CLAIM_CREATED.finditer(message):
        claimed_path = match.group(1)
        if claimed_path in evidence.files_modified and claimed_path not in evidence.files_created:
            corrections.append(
                f'Correction: "{claimed_path}" already existed and was modified, not created.'
            )

    lines = [evidence.render_structured_report()]
    if corrections:
        lines.append("")
        lines.extend(corrections)
    lines.append("")
    lines.append(f'Model\'s own summary (unverified — see above for what was actually confirmed): "{message}"')
    return "\n".join(lines)
