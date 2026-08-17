import subprocess
from pathlib import Path

from skytrap.core.context import WorkspaceContext
from skytrap.tools.base import Tool, ToolResult
from skytrap.tools.filesystem import resolve_in_workspace


def _run_git(args: list[str], workspace: WorkspaceContext) -> ToolResult:
    if not workspace.is_git:
        return ToolResult(success=False, output="This workspace is not a git repository")
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=workspace.path,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.SubprocessError as exc:
        return ToolResult(success=False, output=f"git command failed: {exc}")

    if result.returncode != 0:
        return ToolResult(success=False, output=f"git error: {result.stderr.strip()}")
    return ToolResult(success=True, output=result.stdout)


class GitStatusTool(Tool):
    name = "git_status"
    description = (
        "Show the working tree status (staged, unstaged, and untracked files). "
        "No arguments."
    )

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        result = _run_git(["status", "--short", "--branch"], workspace)
        if result.success and not result.output.strip():
            return ToolResult(success=True, output="Working tree clean, nothing to commit.")
        return result


class GitDiffTool(Tool):
    name = "git_diff"
    description = (
        "Show unstaged changes (or staged changes if staged=true), optionally scoped "
        'to one file. Arguments: {"path": "<optional file path>", "staged": <optional bool>}'
    )

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        args = ["diff"]
        if arguments.get("staged"):
            args.append("--staged")

        path_arg = arguments.get("path")
        if path_arg:
            ok, resolved = resolve_in_workspace(workspace, path_arg)
            if not ok:
                return ToolResult(success=False, output=resolved)
            args += ["--", str(Path(resolved).relative_to(workspace.path))]

        result = _run_git(args, workspace)
        if result.success and not result.output.strip():
            return ToolResult(success=True, output="No differences.")
        return result
