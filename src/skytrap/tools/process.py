import shlex
import time
from typing import Callable

from skytrap.core import processes
from skytrap.core.context import WorkspaceContext
from skytrap.tools.base import Tool, ToolResult

STARTUP_CHECK_DELAY_SECONDS = 1


class StartBackgroundProcessTool(Tool):
    name = "start_background_process"
    description = (
        "Start a long-running background process (e.g. a dev server) that keeps "
        "running after this command finishes, so a URL it serves can be audited "
        "later with lighthouse_audit/accessibility_check. Requires user confirmation. "
        'Arguments: {"command": "<shell command, e.g. \'npm run dev\'>"}'
    )

    def __init__(self, confirm: Callable[[str], bool]):
        self._confirm = confirm

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        command = arguments.get("command")
        if not command:
            return ToolResult(success=False, output="Missing required argument 'command'")

        try:
            tokens = shlex.split(command)
        except ValueError as exc:
            return ToolResult(success=False, output=f"Could not parse command: {exc}")
        if not tokens:
            return ToolResult(success=False, output="Empty command")

        preview = f"Workspace: {workspace.path}\nBackground command: {command}"
        if not self._confirm(preview):
            return ToolResult(success=False, output="User declined to start this process.")

        record = processes.start_process(str(workspace.path), tokens)

        # Give it a moment, then check it didn't just exit immediately (e.g. command
        # not found, missing dependency) — otherwise "started" would be a false claim.
        time.sleep(STARTUP_CHECK_DELAY_SECONDS)
        if not processes.is_running(record.pid):
            log_tail = processes.tail_log(record)
            return ToolResult(
                success=False,
                output=f"Process exited immediately (pid {record.pid}). Recent log:\n{log_tail}",
            )

        return ToolResult(
            success=True,
            output=(
                f"Started background process #{record.id} (pid {record.pid}): {command}\n"
                f"Log: {record.log_path}\n"
                "Use list_background_processes to check status or read logs, "
                "stop_background_process to stop it."
            ),
        )


class ListBackgroundProcessesTool(Tool):
    name = "list_background_processes"
    description = (
        "List all tracked background processes (across all workspaces), or — if given "
        "an id — show recent log output for that one. "
        'Arguments: {"id": <optional process id>, "log_lines": <optional int, default 50>}'
    )

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        process_id = arguments.get("id")
        if process_id is not None:
            return self._describe_one(int(process_id), int(arguments.get("log_lines", 50)))

        records = processes.list_processes()
        if not records:
            return ToolResult(success=True, output="No tracked background processes.")

        lines = [
            f"#{r.id} [{'running' if r.running else 'stopped'}] pid {r.pid}: "
            f"{r.command} ({r.workspace_path})"
            for r in records
        ]
        return ToolResult(success=True, output="\n".join(lines))

    @staticmethod
    def _describe_one(process_id: int, log_lines: int) -> ToolResult:
        record = processes.get_process(process_id)
        if record is None:
            return ToolResult(success=False, output=f"No tracked process with id {process_id}")

        status = "running" if record.running else "stopped"
        tail = processes.tail_log(record, lines=log_lines)
        output = (
            f"#{record.id} [{status}] pid {record.pid}: {record.command}\n"
            f"Workspace: {record.workspace_path}\nStarted: {record.started_at}\n\n"
            f"Recent log:\n{tail}"
        )
        return ToolResult(success=True, output=output)


class StopBackgroundProcessTool(Tool):
    name = "stop_background_process"
    description = (
        "Stop a tracked background process by id (see list_background_processes). "
        'Arguments: {"id": <process id>}'
    )

    def __init__(self, confirm: Callable[[str], bool]):
        self._confirm = confirm

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        process_id = arguments.get("id")
        if process_id is None:
            return ToolResult(success=False, output="Missing required argument 'id'")

        record = processes.get_process(int(process_id))
        if record is None:
            return ToolResult(success=False, output=f"No tracked process with id {process_id}")

        preview = f"Stop process #{record.id} (pid {record.pid}): {record.command}"
        if not self._confirm(preview):
            return ToolResult(success=False, output="User declined to stop this process.")

        ok, message = processes.stop_process(int(process_id))
        return ToolResult(success=ok, output=message)
