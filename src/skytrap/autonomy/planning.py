from __future__ import annotations

import json
import re
from enum import StrEnum

from pydantic import BaseModel, Field, ValidationError

from skytrap.autonomy.intent import HumanIntentEngine, NormalizedIntent
from skytrap.core.context import WorkspaceContext
from skytrap.core.project_inspection import inspect_project, resolve_commands
from skytrap.core.repo_map import build_repo_map
from skytrap.models.base import ModelProvider


class PlanStepStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


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
    tests: list[str] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    revision: int = 1


PLANNER_PROMPT = """You are SkyTrap's planner. Produce a concrete plan for the goal.
Return JSON only, matching this shape:
{"summary":"...","steps":[{"id":"step-1","description":"...","files":[],
"commands":[],"risks":[],"success_criteria":[]}],"files":[],"tests":[],
"commands":[],"risks":[],"success_criteria":[],"revision":1}
Name only relevant files. Include explicit validation criteria. Do not implement.
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
    def __init__(self, model: ModelProvider):
        self.model = model

    def create_plan(
        self, workspace: WorkspaceContext, intent: NormalizedIntent | str
    ) -> TaskPlan:
        # String compatibility keeps third-party integrations working, while the
        # autonomous runtime always supplies the structured contract.
        if isinstance(intent, str):
            intent = HumanIntentEngine().normalize(intent, workspace=workspace)
        profile = inspect_project(workspace)
        commands: list[str] = []
        for match in profile.languages[:3]:
            resolved = resolve_commands(workspace, match)
            commands.extend(resolved.lint_commands)
            if resolved.check_command:
                commands.append(resolved.check_command)
            commands.extend(resolved.test_commands)
            commands.extend(resolved.build_commands)

        prompt = (
            "Normalized human intent (authoritative contract; raw_input is retained as evidence):\n"
            f"{intent.model_dump_json(indent=2)}\n\nRepository map:\n{build_repo_map(workspace)}\n\n"
            f"Detected validation commands:\n" + "\n".join(dict.fromkeys(commands))
        )
        raw = self.model.chat(
            [{"role": "system", "content": PLANNER_PROMPT}, {"role": "user", "content": prompt}]
        )
        data = _json_object(raw)
        if data is not None:
            try:
                return TaskPlan.model_validate(data)
            except ValidationError:
                pass

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
            commands=list(dict.fromkeys(commands)),
            tests=[command for command in commands if "test" in command or "pytest" in command],
            risks=[
                *intent.contradictions,
                *intent.ambiguities,
                "Model output was not a valid structured plan; deterministic fallback used",
            ],
            success_criteria=["Implementation is present", "All available verification commands pass"],
        )

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
