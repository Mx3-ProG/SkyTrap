from __future__ import annotations

import subprocess

from skytrap.autonomy.memory import WorkingMemory
from skytrap.autonomy.state import TaskState
from skytrap.core.context import WorkspaceContext
from skytrap.tools.base import ToolResult


class GitWorkflow:
    """Dedicated-branch lifecycle for an autonomous local task."""

    def _git(self, workspace: WorkspaceContext, *args: str) -> ToolResult:
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=workspace.path,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (FileNotFoundError, subprocess.SubprocessError) as exc:
            return ToolResult(success=False, output=f"git failed: {exc}", stderr=str(exc))
        output = completed.stdout.strip() or completed.stderr.strip()
        return ToolResult(
            success=completed.returncode == 0,
            output=output or "git command succeeded",
            stdout=completed.stdout,
            stderr=completed.stderr,
            exit_code=completed.returncode,
            metadata={"command": ["git", *args]},
        )

    def prepare(self, workspace: WorkspaceContext, task: TaskState) -> ToolResult:
        if not workspace.is_git:
            return ToolResult(success=False, output="Autonomous tasks require a Git repository")
        status = self._git(workspace, "status", "--porcelain")
        if not status.success:
            return status
        if status.stdout.strip():
            return ToolResult(
                success=False,
                output="Working tree must be clean before creating an autonomous task branch",
                stdout=status.stdout,
            )
        branch = self._git(workspace, "branch", "--show-current")
        base = self._git(workspace, "rev-parse", "HEAD")
        if not branch.success or not base.success:
            return branch if not branch.success else base
        task.original_branch = branch.stdout.strip() or None
        task.base_commit = base.stdout.strip()
        task.task_branch = f"skytrap/task-{task.task_id}"
        switched = self._git(workspace, "switch", "-c", task.task_branch)
        if switched.success:
            switched.metadata.update(
                {"original_branch": task.original_branch, "task_branch": task.task_branch, "base_commit": task.base_commit}
            )
        return switched

    def ensure_task_branch(self, workspace: WorkspaceContext, task: TaskState) -> ToolResult:
        if not task.task_branch:
            return ToolResult(success=False, output="Task has no dedicated branch")
        current = self._git(workspace, "branch", "--show-current")
        if not current.success:
            return current
        if current.stdout.strip() == task.task_branch:
            return ToolResult(success=True, output=f"Already on {task.task_branch}")
        status = self._git(workspace, "status", "--porcelain")
        if not status.success or status.stdout.strip():
            return ToolResult(success=False, output="Cannot switch task branch with a dirty working tree")
        return self._git(workspace, "switch", task.task_branch)

    def checkpoint(
        self, workspace: WorkspaceContext, task: TaskState, memory: WorkingMemory
    ) -> ToolResult:
        branch = self.ensure_task_branch(workspace, task)
        if not branch.success:
            return branch
        tracked = self._git(workspace, "diff", "--name-only", "--")
        if not tracked.success:
            return tracked
        paths = list(
            dict.fromkeys(
                [*memory.files_modified, *tracked.stdout.splitlines()]
            )
        )
        if not paths:
            task.checkpoint_commit = task.base_commit
            task.final_diff = "No changes."
            return ToolResult(
                success=True,
                output="Verification passed; no workspace changes required",
                metadata={"checkpoint_commit": task.checkpoint_commit, "diff": task.final_diff},
            )
        staged = self._git(workspace, "add", "-A", "--", *paths)
        if not staged.success:
            return staged
        staged_diff = self._git(workspace, "diff", "--cached", "--binary", "--")
        if not staged_diff.success:
            return staged_diff
        if not staged_diff.stdout.strip():
            task.checkpoint_commit = task.base_commit
            task.final_diff = "No changes."
            return ToolResult(
                success=True,
                output="Verification passed; no workspace changes required",
                metadata={"checkpoint_commit": task.checkpoint_commit, "diff": task.final_diff},
            )
        committed = self._git(
            workspace,
            "-c",
            "user.name=SkyTrap",
            "-c",
            "user.email=skytrap@local",
            "commit",
            "-m",
            f"skytrap: checkpoint task {task.task_id}",
        )
        if not committed.success:
            return committed
        head = self._git(workspace, "rev-parse", "HEAD")
        final_diff = self._git(workspace, "diff", "--binary", f"{task.base_commit}..HEAD", "--")
        if not head.success or not final_diff.success:
            return head if not head.success else final_diff
        task.checkpoint_commit = head.stdout.strip()
        task.final_diff = final_diff.stdout or "No differences."
        memory.git_state = f"checkpoint {task.checkpoint_commit} on {task.task_branch}"
        return ToolResult(
            success=True,
            output=f"Checkpoint created: {task.checkpoint_commit}",
            stdout=task.final_diff,
            metadata={"checkpoint_commit": task.checkpoint_commit, "diff": task.final_diff},
        )

    def rollback(
        self, workspace: WorkspaceContext, task: TaskState, memory: WorkingMemory
    ) -> ToolResult:
        if not task.base_commit:
            return ToolResult(success=False, output="Task has no base commit")
        branch = self.ensure_task_branch(workspace, task)
        if not branch.success:
            return branch
        reset = self._git(workspace, "reset", "--hard", task.base_commit)
        if not reset.success:
            return reset
        task.rolled_back = True
        memory.git_state = f"rolled back {task.task_branch} to {task.base_commit}"
        return ToolResult(success=True, output=memory.git_state)
