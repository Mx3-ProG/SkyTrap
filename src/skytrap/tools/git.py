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


def run_git_diff(
    workspace: WorkspaceContext, paths: list[str] | None = None, staged: bool = False
) -> ToolResult:
    """Diffs the given paths (or the whole workspace if none given). Used directly by
    `skytrap build` to scope the Reviewer summary to only the files the Developer role
    actually touched, not the whole working tree."""
    args = ["diff"]
    if staged:
        args.append("--staged")

    if paths:
        resolved_paths = []
        for path_arg in paths:
            ok, resolved = resolve_in_workspace(workspace, path_arg)
            if not ok:
                return ToolResult(success=False, output=resolved)
            resolved_paths.append(str(Path(resolved).relative_to(workspace.path)))
        args += ["--", *resolved_paths]

    result = _run_git(args, workspace)
    if result.success and not result.output.strip():
        return ToolResult(success=True, output="No differences.")
    return result


class GitDiffTool(Tool):
    name = "git_diff"
    description = (
        "Show unstaged changes (or staged changes if staged=true), optionally scoped "
        'to one file. Arguments: {"path": "<optional file path>", "staged": <optional bool>}'
    )

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        path_arg = arguments.get("path")
        return run_git_diff(
            workspace,
            paths=[path_arg] if path_arg else None,
            staged=bool(arguments.get("staged")),
        )


def review_diff(workspace: WorkspaceContext, paths: list[str]) -> ToolResult:
    """Diff summary for the `build` command's Reviewer step. Plain `git diff` shows
    nothing for untracked (newly created) files — there's no tracked baseline to diff
    against — which would silently hide exactly the files write_file just created. This
    shows the full content for those instead of an empty diff.
    """
    if not workspace.is_git:
        return ToolResult(success=False, output="This workspace is not a git repository")

    sections = []
    for path_arg in paths:
        ok, resolved = resolve_in_workspace(workspace, path_arg)
        if not ok:
            sections.append(f"{path_arg}: {resolved}")
            continue
        rel = str(Path(resolved).relative_to(workspace.path))

        status = _run_git(["status", "--porcelain", "--", rel], workspace)
        is_untracked = status.success and status.output.strip().startswith("??")

        if is_untracked:
            try:
                content = Path(resolved).read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                content = "(binary or unreadable file)"
            sections.append(f"--- NEW FILE: {rel} ---\n{content}")
        else:
            diff = _run_git(["diff", "--", rel], workspace)
            sections.append(diff.output if diff.success else f"{rel}: {diff.output}")

    joined = "\n\n".join(section for section in sections if section.strip())
    return ToolResult(success=True, output=joined or "No differences.")
