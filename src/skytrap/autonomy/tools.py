from __future__ import annotations

from collections.abc import Callable

from skytrap.autonomy.patching import PatchEngine
from skytrap.autonomy.verification import VerificationLoop, VerificationStage
from skytrap.core.context import WorkspaceContext
from skytrap.tools.base import Tool, ToolResult
from skytrap.tools.filesystem import DeleteFileTool, ListDirectoryTool, ReadFileTool, WriteFileTool
from skytrap.tools.git import GitDiffTool, GitStatusTool
from skytrap.tools.process import (
    ListBackgroundProcessesTool,
    StartBackgroundProcessTool,
    StopBackgroundProcessTool,
)
from skytrap.tools.project import InspectProjectTool
from skytrap.tools.search import SearchCodeTool
from skytrap.tools.shell import ShellTool
from skytrap.tools.structural_search import StructuralSearchTool


class PatchFileTool(Tool):
    name = "patch_file"
    description = (
        "Replace exactly one matching text block in a file, with conflict detection "
        "and rollback metadata. Prefer this over rewriting an existing file. "
        'Arguments: {"path":"relative/path", "expected":"old text", '
        '"replacement":"new text", "expected_hash":"optional sha256"}'
    )

    def __init__(self, engine: PatchEngine):
        self.engine = engine

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        path = arguments.get("path")
        expected = arguments.get("expected")
        replacement = arguments.get("replacement")
        if not path:
            return ToolResult(success=False, output="Missing required argument 'path'")
        if expected is None:
            return ToolResult(success=False, output="Missing required argument 'expected'")
        if replacement is None:
            return ToolResult(success=False, output="Missing required argument 'replacement'")
        return self.engine.apply_replacement(
            workspace,
            path,
            expected,
            replacement,
            expected_hash=arguments.get("expected_hash"),
        )


class ListFilesTool(ListDirectoryTool):
    name = "list_files"
    description = (
        "List immediate files and subdirectories without leaving the authorized workspace. "
        'Arguments: {"path":"relative directory, or . for the root"}'
    )


class VerificationTool(Tool):
    def __init__(
        self,
        name: str,
        stage: VerificationStage,
        verifier: VerificationLoop,
    ):
        self.name = name
        self.stage = stage
        self.verifier = verifier
        self.description = (
            f"Run all configured {stage.value} commands detected for the workspace. "
            'Arguments: {"commands": ["optional", "override"]}'
        )

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        override = arguments.get("commands")
        if override is not None and not isinstance(override, list):
            return ToolResult(success=False, output="'commands' must be a list of command strings")
        commands = override if override is not None else self.verifier.discover(workspace)[self.stage]
        if not commands:
            return ToolResult(
                success=True,
                output=f"No configured {self.stage.value} command; stage skipped",
                metadata={"stage": self.stage.value, "skipped": True, "executed_count": 0},
            )
        selected = {stage: [] for stage in VerificationStage}
        selected[self.stage] = commands
        verification = self.verifier.run(workspace, selected)
        output = "\n\n".join(result.output for result in verification.results)
        return ToolResult(
            success=verification.success,
            output=output,
            stdout="\n".join(result.stdout for result in verification.results),
            stderr="\n".join(result.stderr for result in verification.results),
            exit_code=verification.results[-1].exit_code if verification.results else None,
            metadata={
                "stage": self.stage.value,
                "skipped": False,
                "executed_count": len(verification.results),
                "commands": commands,
                "results": [result.model_dump(mode="json") for result in verification.results],
            },
        )


class LintTool(VerificationTool):
    def __init__(self, verifier: VerificationLoop):
        super().__init__("lint", VerificationStage.LINT, verifier)


class TypecheckTool(VerificationTool):
    def __init__(self, verifier: VerificationLoop):
        super().__init__("typecheck", VerificationStage.TYPECHECK, verifier)


class TestTool(VerificationTool):
    def __init__(self, verifier: VerificationLoop):
        super().__init__("run_tests", VerificationStage.TEST, verifier)


class BuildTool(VerificationTool):
    def __init__(self, verifier: VerificationLoop):
        super().__init__("build", VerificationStage.BUILD, verifier)


def build_autonomous_tools(
    verifier: VerificationLoop,
    patch_engine: PatchEngine | None = None,
    confirm: Callable[[str], bool] | None = None,
) -> list[Tool]:
    """One canonical toolset for CLI/server autonomous tasks.

    Individual tools receive an always-approve callback because ToolExecutor is the
    single policy and approval boundary. ShellTool still applies its hard forbidden
    command policy internally as defense in depth.
    """

    allow = confirm or (lambda _preview: True)
    engine = patch_engine or PatchEngine()
    return [
        ReadFileTool(),
        ListDirectoryTool(),
        ListFilesTool(),
        SearchCodeTool(),
        StructuralSearchTool(),
        InspectProjectTool(),
        GitStatusTool(),
        GitDiffTool(),
        WriteFileTool(confirm=allow),
        PatchFileTool(engine),
        DeleteFileTool(confirm=allow),
        ShellTool(confirm=allow, confirm_destructive=allow),
        StartBackgroundProcessTool(confirm=allow),
        ListBackgroundProcessesTool(),
        StopBackgroundProcessTool(confirm=allow),
        LintTool(verifier),
        TypecheckTool(verifier),
        TestTool(verifier),
        BuildTool(verifier),
    ]
