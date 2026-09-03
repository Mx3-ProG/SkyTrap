import subprocess
from pathlib import Path

from pydantic import BaseModel


class WorkspaceContext(BaseModel):
    path: Path
    name: str
    is_git: bool
    branch: str | None = None


def _run_git(args: list[str], cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def detect_workspace(cwd: Path | None = None) -> WorkspaceContext:
    """Detects the current directory as the SkyTrap workspace, including git state."""
    cwd = (cwd or Path.cwd()).resolve()

    repo_root = _run_git(["rev-parse", "--show-toplevel"], cwd)
    is_git = repo_root is not None
    branch = _run_git(["branch", "--show-current"], cwd) if is_git else None

    return WorkspaceContext(
        path=cwd,
        name=cwd.name,
        is_git=is_git,
        branch=branch or None,
    )


def git_worktree_state(workspace: WorkspaceContext) -> str:
    """Return a compact, truthful Git state for terminal status displays."""
    if not workspace.is_git:
        return "not a git repository"
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workspace.path,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return "unavailable"
    if result.returncode != 0:
        return "unavailable"
    return "dirty" if result.stdout.strip() else "clean"
