from __future__ import annotations

import json
import re
from enum import StrEnum

from pydantic import BaseModel, Field, ValidationError

from skytrap.autonomy.intent import HumanIntentEngine, NormalizedIntent
from skytrap.core.context import WorkspaceContext
from skytrap.core.capabilities import CapabilityMatrix, detect_runtime_capabilities
from skytrap.core.project_inspection import resolve_commands
from skytrap.intelligence.duplication import ExistingCapabilityDetector
from skytrap.intelligence.existence import ExistenceEvidence, ExistenceStatus, check_existence
from skytrap.intelligence.policy import policy_prompt
from skytrap.intelligence.snapshot import RepositorySnapshot, build_repository_snapshot
from skytrap.intelligence.symbols import SymbolIndex


class PlanStepStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class FileActionType(StrEnum):
    KEEP = "keep"
    REUSE = "reuse"
    MODIFY = "modify"
    CREATE = "create"
    DELETE = "delete"


class PlanFileAction(BaseModel):
    """Item 11/15 — the plan must classify every file it touches instead of
    defaulting to "generate files". A CREATE with no justification, for a
    path that existence evidence says already EXISTS or is PARTIAL, is what
    produced the original bug (re-"creating" index.html)."""

    path: str
    action: FileActionType
    justification: str = ""


class PlanStep(BaseModel):
    id: str
    description: str
    files: list[str] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    status: PlanStepStatus = PlanStepStatus.PENDING


class TaskPlan(BaseModel):
    summary: str
    steps: list[PlanStep]
    files: list[str] = Field(default_factory=list)
    file_actions: list[PlanFileAction] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    revision: int = 1


PLANNER_PROMPT = """You are SkyTrap's planner. You will be given real evidence about what
already exists in this repository — never assume a file/feature is missing just because you
haven't seen it named explicitly. Produce a concrete plan for the goal that follows the
principle: prefer modification over duplication, prefer extension over replacement, prefer a
targeted patch over a full rewrite, and preserve the existing architecture unless there is
concrete evidence it should change.

Return JSON only, matching this shape:
{"summary":"...","steps":[{"id":"step-1","description":"...","files":[],
"commands":[],"risks":[],"success_criteria":[]}],"files":[],
"file_actions":[{"path":"...","action":"keep|reuse|modify|create|delete","justification":"..."}],
"tests":[],"commands":[],"risks":[],"success_criteria":[],"revision":1}

Every file you name in "files" must have a matching entry in "file_actions". A "create" action
is only valid when the existing evidence says the file/feature is MISSING — if it says EXISTS or
PARTIAL, use "modify"/"reuse"/"keep" instead and explain why in "justification". Do not implement
— this is a plan only.
"""


def _json_object(raw: str) -> dict | None:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


class Planner:
    def __init__(self, model, capability_matrix: CapabilityMatrix | None = None):
        self.model = model
        self.capability_matrix = capability_matrix or detect_runtime_capabilities()

    def create_plan(
        self,
        workspace: WorkspaceContext,
        intent: NormalizedIntent | str,
        *,
        snapshot: RepositorySnapshot | None = None,
        existence_evidence: list[ExistenceEvidence] | None = None,
        symbol_index: SymbolIndex | None = None,
    ) -> TaskPlan:
        # String compatibility keeps third-party integrations working, while the
        # autonomous runtime always supplies the structured contract.
        if isinstance(intent, str):
            intent = HumanIntentEngine().normalize(intent, workspace=workspace)

        snapshot = snapshot or build_repository_snapshot(workspace)
        if existence_evidence is None:
            existence_evidence = [
                check_existence(workspace, snapshot, entity)
                for entity in intent.referenced_entities[:8]
            ]

        commands: list[str] = []
        for match in _profile_languages(workspace)[:3]:
            resolved = resolve_commands(workspace, match)
            commands.extend(resolved.lint_commands)
            if resolved.check_command:
                commands.append(resolved.check_command)
            commands.extend(resolved.test_commands)
            commands.extend(resolved.build_commands)

        evidence_block = "\n".join(f"- {line}" for line in _evidence_lines(snapshot, existence_evidence))

        prompt = (
            "Normalized human intent (authoritative contract; raw_input is retained as evidence):\n"
            f"{intent.model_dump_json(indent=2)}\n\n"
            f"Existing evidence:\n{evidence_block}\n\n"
            f"Repository files ({len(snapshot.files)} total{' — truncated' if snapshot.truncated else ''}):\n"
            + "\n".join(snapshot.files[:400])
            + f"\n\nDetected validation commands:\n"
            + "\n".join(dict.fromkeys(commands))
            + f"\n\n{self.capability_matrix.planner_prompt()}"
            + f"\n\n{policy_prompt(snapshot.languages)}"
        )
        raw = self.model.chat(
            [{"role": "system", "content": PLANNER_PROMPT}, {"role": "user", "content": prompt}]
        )
        data = _json_object(raw)
        if data is not None:
            try:
                plan = TaskPlan.model_validate(data)
                plan = _reconcile_file_actions(plan, existence_evidence)
                return _reconcile_duplication(workspace, snapshot, plan, intent, symbol_index)
            except ValidationError:
                pass

        plan = _fallback_plan(intent, commands, existence_evidence)
        return _reconcile_duplication(workspace, snapshot, plan, intent, symbol_index)

    def revise_plan(self, plan: TaskPlan, failure: str) -> TaskPlan:
        revised = plan.model_copy(deep=True)
        revised.revision += 1
        revised.steps.append(
            PlanStep(
                id=f"repair-{revised.revision}",
                description="Diagnose and repair the latest verification failure",
                success_criteria=["The failing verification command passes"],
                risks=[failure[-1000:]],
            )
        )
        return revised


def _profile_languages(workspace: WorkspaceContext):
    from skytrap.core.project_inspection import inspect_project

    return inspect_project(workspace).languages


def _evidence_lines(snapshot: RepositorySnapshot, evidence: list[ExistenceEvidence]) -> list[str]:
    lines = list(snapshot.evidence_lines())
    lines.extend(item.as_bullet() for item in evidence)
    return lines


def _reconcile_file_actions(plan: TaskPlan, evidence: list[ExistenceEvidence]) -> TaskPlan:
    """Deterministic safety net on top of the model's own plan: a CREATE for a
    path that existence evidence says already EXISTS is downgraded to MODIFY
    and flagged — this is the concrete fix for "announced creating X that
    already existed"."""
    exists_paths: set[str] = set()
    for item in evidence:
        if item.status in {ExistenceStatus.EXISTS, ExistenceStatus.PARTIAL}:
            exists_paths.update(item.matched_files)

    corrected: list[PlanFileAction] = []
    for action in plan.file_actions:
        if action.action == FileActionType.CREATE and action.path in exists_paths:
            corrected.append(
                PlanFileAction(
                    path=action.path,
                    action=FileActionType.MODIFY,
                    justification=(
                        f"Corrected from 'create': existing evidence shows {action.path} already exists — "
                        + (action.justification or "modifying instead of duplicating.")
                    ),
                )
            )
        else:
            corrected.append(action)
    plan.file_actions = corrected
    return plan


def _reconcile_duplication(
    workspace: WorkspaceContext,
    snapshot: RepositorySnapshot,
    plan: TaskPlan,
    intent: NormalizedIntent,
    symbol_index: SymbolIndex | None,
) -> TaskPlan:
    """Item 4 — SEMANTIC DUPLICATION DETECTOR. `_reconcile_file_actions` only
    catches a CREATE whose *own path* existence evidence already flagged
    (e.g. re-"creating" index.html). This catches the other half of the gap:
    a CREATE at a *new* path that duplicates a capability implemented
    elsewhere under a different name (e.g. proposing src/services/auth.ts
    when src/auth/authService.ts already exists)."""
    if symbol_index is None:
        return plan
    detector = ExistingCapabilityDetector(symbol_index=symbol_index)
    corrected: list[PlanFileAction] = []
    for action in plan.file_actions:
        if action.action != FileActionType.CREATE:
            corrected.append(action)
            continue
        description = f"{intent.interpreted_goal} {action.justification}".strip()
        evidence = detector.check(workspace, snapshot, description=description, proposed_path=action.path)
        if evidence.status in {ExistenceStatus.EXISTS, ExistenceStatus.PARTIAL}:
            where = ", ".join(evidence.matched_files or evidence.matched_symbols)
            corrected.append(
                PlanFileAction(
                    path=action.path,
                    action=FileActionType.REUSE if evidence.status == ExistenceStatus.EXISTS else FileActionType.MODIFY,
                    justification=(
                        f"Corrected from 'create': {evidence.reason} ({where}) — "
                        "reuse/extend that implementation instead of duplicating it under a new name."
                    ),
                )
            )
        else:
            corrected.append(action)
    plan.file_actions = corrected
    return plan


def _fallback_plan(
    intent: NormalizedIntent, commands: list[str], evidence: list[ExistenceEvidence]
) -> TaskPlan:
    file_actions = [
        PlanFileAction(
            path=item.matched_files[0] if item.matched_files else item.query,
            action=(
                FileActionType.MODIFY
                if item.status in {ExistenceStatus.EXISTS, ExistenceStatus.PARTIAL}
                else FileActionType.CREATE
            ),
            justification=item.as_bullet(),
        )
        for item in evidence
        if item.status != ExistenceStatus.UNKNOWN
    ]
    return TaskPlan(
        summary=f"Inspect, implement and verify: {intent.interpreted_goal}",
        steps=[
            PlanStep(
                id="step-1",
                description="Inspect relevant code and implement the requested change",
                success_criteria=["The requested behavior is implemented in the workspace"],
            ),
            PlanStep(
                id="step-2",
                description="Run the detected verification commands and repair failures",
                commands=list(dict.fromkeys(commands)),
                success_criteria=["All available verification commands pass"],
            ),
        ],
        file_actions=file_actions,
        commands=list(dict.fromkeys(commands)),
        tests=[command for command in commands if "test" in command or "pytest" in command],
        risks=[
            *intent.contradictions,
            *intent.ambiguities,
            "Model output was not a valid structured plan; deterministic fallback used",
        ],
        success_criteria=["Implementation is present", "All available verification commands pass"],
    )
