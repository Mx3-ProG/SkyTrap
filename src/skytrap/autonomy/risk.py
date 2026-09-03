from __future__ import annotations

from enum import IntEnum, StrEnum

from pydantic import BaseModel, Field

from skytrap.autonomy.intent import IntentRisk, NormalizedIntent
from skytrap.core.tool_safety import classify_command, classify_path


class RiskLevel(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class Capability(StrEnum):
    FILESYSTEM_READ = "filesystem:read"
    FILESYSTEM_WRITE = "filesystem:write"
    SHELL_EXECUTE = "shell:execute"
    GIT_COMMIT = "git:commit"
    GIT_PUSH = "git:push"
    DEPLOY_EXECUTE = "deploy:execute"
    SECRETS_USE = "secrets:use"


FULL_INTERACTIVE_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.FILESYSTEM_READ,
        Capability.FILESYSTEM_WRITE,
        Capability.SHELL_EXECUTE,
        Capability.SECRETS_USE,
        Capability.GIT_COMMIT,
        Capability.GIT_PUSH,
    }
)
"""Item 1 — UNIFY EXECUTION: the one capability set every mutating execution path
(interactive chat, `skytrap build`, the web server, and `skytrap agent run`)
grants its ToolExecutor. A capability missing here means the *action is silently
denied before approval is ever asked* — that's a stronger gate than
RiskAssessment.requires_approval and must not be confused with it. Historically
only {FILESYSTEM_READ, FILESYSTEM_WRITE, SHELL_EXECUTE} were granted, which meant
a secrets-path write (capability=SECRETS_USE) or a `git commit`/`git push` shell
command (capability=GIT_COMMIT/GIT_PUSH) was denied outright rather than routed
to approval — never actually exercised because interactive mode's now-removed
per-tool confirm() used to gate those cases entirely on its own. DEPLOY_EXECUTE
is deliberately excluded: nothing in RiskEngine currently assigns it, and it
would need an explicit, separate opt-in if that changes."""


class RiskAssessment(BaseModel):
    level: RiskLevel
    capability: Capability
    reasons: list[str] = Field(default_factory=list)
    requires_approval: bool = False


READ_TOOLS = {"read_file", "list_directory", "list_files", "search_code", "git_status", "git_diff", "inspect_project"}
WRITE_TOOLS = {"write_file", "patch_file", "delete_file"}


class RiskEngine:
    """Classifies requested actions before an executor sees credentials or runs code."""

    def assess(
        self,
        tool_name: str,
        arguments: dict,
        intent: NormalizedIntent | None = None,
    ) -> RiskAssessment:
        assessment = self._assess_tool(tool_name, arguments)
        if intent is None:
            return assessment
        return self._combine_with_intent(assessment, intent)

    def _assess_tool(self, tool_name: str, arguments: dict) -> RiskAssessment:
        if tool_name in READ_TOOLS:
            return RiskAssessment(level=RiskLevel.LOW, capability=Capability.FILESYSTEM_READ)

        if tool_name in {"run_tests", "lint", "typecheck", "build"}:
            return RiskAssessment(
                level=RiskLevel.LOW,
                capability=Capability.SHELL_EXECUTE,
                reasons=["Verification executes project code in the authorized workspace"],
            )

        if tool_name in WRITE_TOOLS:
            path = str(arguments.get("path", ""))
            if classify_path(path) == "DESTRUCTIVE":
                return RiskAssessment(
                    level=RiskLevel.CRITICAL,
                    capability=Capability.SECRETS_USE,
                    reasons=["The target path may contain credentials or secrets"],
                    requires_approval=True,
                )
            level = RiskLevel.HIGH if tool_name == "delete_file" else RiskLevel.MEDIUM
            return RiskAssessment(
                level=level,
                capability=Capability.FILESYSTEM_WRITE,
                reasons=["The action changes workspace files"],
                requires_approval=level >= RiskLevel.HIGH,
            )

        if tool_name == "shell":
            command = str(arguments.get("command", ""))
            tier = classify_command(command)
            if tier == "FORBIDDEN":
                return RiskAssessment(
                    level=RiskLevel.CRITICAL,
                    capability=Capability.SHELL_EXECUTE,
                    reasons=["The command is forbidden by policy"],
                    requires_approval=True,
                )
            if command.startswith("git push"):
                capability = Capability.GIT_PUSH
                level = RiskLevel.HIGH
            elif command.startswith("git commit"):
                capability = Capability.GIT_COMMIT
                level = RiskLevel.MEDIUM
            else:
                capability = Capability.SHELL_EXECUTE
                level = {"SAFE": RiskLevel.LOW, "CONFIRM": RiskLevel.MEDIUM, "DESTRUCTIVE": RiskLevel.CRITICAL}[tier]
            return RiskAssessment(
                level=level,
                capability=capability,
                reasons=[f"Command policy tier: {tier}"],
                requires_approval=level >= RiskLevel.HIGH,
            )

        return RiskAssessment(
            level=RiskLevel.MEDIUM,
            capability=Capability.SHELL_EXECUTE,
            reasons=["Unknown tool defaults to constrained execution"],
        )

    @staticmethod
    def _combine_with_intent(
        assessment: RiskAssessment, intent: NormalizedIntent
    ) -> RiskAssessment:
        intent_level = {
            IntentRisk.LOW: RiskLevel.LOW,
            IntentRisk.MEDIUM: RiskLevel.MEDIUM,
            IntentRisk.HIGH: RiskLevel.HIGH,
            IntentRisk.CRITICAL: RiskLevel.CRITICAL,
        }[intent.risk]
        mutating = assessment.capability in {
            Capability.FILESYSTEM_WRITE,
            Capability.GIT_COMMIT,
            Capability.GIT_PUSH,
            Capability.DEPLOY_EXECUTE,
            Capability.SECRETS_USE,
        }
        level = max(assessment.level, intent_level) if mutating else assessment.level
        reasons = list(assessment.reasons)
        if mutating and intent_level > assessment.level:
            reasons.append(f"Human intent consequence level: {intent.risk.value}")
        if intent.ambiguities and mutating:
            reasons.append("The normalized intent contains unresolved ambiguity")
        if intent.contradictions and mutating:
            reasons.append("The normalized intent contains contradictory constraints")
        return assessment.model_copy(
            update={
                "level": level,
                "reasons": reasons,
                "requires_approval": assessment.requires_approval
                or (mutating and (intent.clarification_required or level >= RiskLevel.HIGH)),
            }
        )
