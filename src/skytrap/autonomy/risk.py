from __future__ import annotations

from enum import IntEnum, StrEnum

from pydantic import BaseModel, Field

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


class RiskAssessment(BaseModel):
    level: RiskLevel
    capability: Capability
    reasons: list[str] = Field(default_factory=list)
    requires_approval: bool = False


READ_TOOLS = {"read_file", "list_directory", "list_files", "search_code", "git_status", "git_diff", "inspect_project"}
WRITE_TOOLS = {"write_file", "patch_file", "delete_file"}


class RiskEngine:
    """Classifies requested actions before an executor sees credentials or runs code."""

    def assess(self, tool_name: str, arguments: dict) -> RiskAssessment:
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
