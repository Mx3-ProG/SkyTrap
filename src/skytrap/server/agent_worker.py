from typing import Callable

from skytrap.core.context import WorkspaceContext
from skytrap.core.notes import run_summarizer
from skytrap.core.project_notes import append_journal_entry
from skytrap.core.roles import run_architect, run_developer, run_reviewer
from skytrap.memory.sqlite import SqliteMemory
from skytrap.models.base import ModelProvider
from skytrap.server.turns import TurnRegistry
from skytrap.server.ws.confirmation_bridge import ConfirmationBridge
from skytrap.tools.base import Tool
from skytrap.tools.filesystem import DeleteFileTool, ListDirectoryTool, ReadFileTool, WriteFileTool
from skytrap.tools.git import GitDiffTool, GitStatusTool, review_diff
from skytrap.tools.notes import GetPastNotesTool
from skytrap.tools.process import (
    ListBackgroundProcessesTool,
    StartBackgroundProcessTool,
    StopBackgroundProcessTool,
)
from skytrap.tools.project import InspectProjectTool
from skytrap.tools.search import SearchCodeTool
from skytrap.tools.security import SecurityAuditTool
from skytrap.tools.shell import ShellTool
from skytrap.tools.tests import RunTestsTool


def build_server_toolset(
    bridge: ConfirmationBridge,
    memory: SqliteMemory | None = None,
    on_write: Callable[[str], None] | None = None,
) -> list[Tool]:
    """Same toolset as cli.py::_build_full_toolset, but every confirm callback
    calls bridge.request(preview, kind) instead of Rich's Confirm.ask() — routes
    the confirmation over the WebSocket instead of the terminal. Deliberately
    duplicated here (~15 lines) rather than shared with cli.py: two callers
    (Rich-terminal confirm vs. WebSocket-bridge confirm) don't justify a common
    abstraction yet, matching this project's stance against premature abstraction.
    `on_write`, if given, is forwarded to WriteFileTool to track touched paths —
    used to scope the Reviewer's diff to only what the Developer actually wrote.
    Skills (registry-based tools) are intentionally excluded for this milestone —
    `normal` mode scope is the base toolset proven against a real confirmation
    round-trip first; skills are a mechanical addition once that's solid.
    """
    tools: list[Tool] = [
        ReadFileTool(),
        ListDirectoryTool(),
        SearchCodeTool(),
        GitStatusTool(),
        GitDiffTool(),
        InspectProjectTool(),
        SecurityAuditTool(),
        WriteFileTool(confirm=lambda preview: bridge.request(preview, "write"), on_write=on_write),
        DeleteFileTool(confirm=lambda preview: bridge.request(preview, "delete")),
        ShellTool(confirm=lambda preview: bridge.request(preview, "shell")),
        RunTestsTool(),
        StartBackgroundProcessTool(confirm=lambda preview: bridge.request(preview, "start_process")),
        ListBackgroundProcessesTool(),
        StopBackgroundProcessTool(confirm=lambda preview: bridge.request(preview, "stop_process")),
    ]
    if memory is not None:
        tools.append(GetPastNotesTool(memory=memory))
    return tools


def run_server_task(
    turn_id: str,
    task: str,
    model: ModelProvider,
    workspace: WorkspaceContext,
    registry: TurnRegistry,
    on_progress: Callable[[dict], None],
    bridge: ConfirmationBridge,
    memory: SqliteMemory,
    session_id: int,
) -> None:
    """Entry point for the background thread spawned by POST /turns. Runs the same
    Architect -> confirm plan -> Developer -> tests -> Reviewer -> Summarizer
    pipeline as `skytrap build` (see cli.py::build), adapted to stream progress and
    route its plan/write/delete/shell confirmations over the WebSocket bridge
    instead of the terminal — this is what lets the agent reason over the whole
    workspace and propose its plan before touching anything, from the phone.

    Each HTTP-triggered turn is independent (fresh Architect/Developer/Reviewer
    history) — no multi-turn conversation state across separate POST /turns calls
    at this milestone, same as the flat loop this replaces.
    """
    registry.mark_running(turn_id)

    try:
        plan_text = run_architect(model, workspace, task, memory=memory, on_step=on_progress)
    except Exception as exc:  # noqa: BLE001 - the turn must never crash the worker thread silently
        registry.mark_error(turn_id, str(exc))
        return

    if not bridge.request(plan_text, "plan"):
        registry.mark_done(turn_id, "Plan declined — no changes made.")
        return

    try:
        touched_files: list[str] = []
        tools = build_server_toolset(bridge, memory=memory, on_write=touched_files.append)
        summary = run_developer(model, tools, workspace, task, plan_text, on_step=on_progress)

        test_result = RunTestsTool().execute(workspace, {})
        on_progress({"tool": "run_tests", "arguments": {}, "observation": test_result.output})

        review = ""
        if touched_files and workspace.is_git:
            diff_result = review_diff(workspace, touched_files)
            if diff_result.success:
                review = run_reviewer(
                    model,
                    workspace,
                    task,
                    diff_result.output,
                    test_result.output,
                    test_result.success,
                    memory=memory,
                    on_step=on_progress,
                )

        outcome = f"Developer summary:\n{summary}"
        if review:
            outcome += f"\n\nReviewer findings:\n{review}"

        if touched_files:
            # Only worth journaling a completed, non-empty piece of work — matches
            # cli.py::build's "end-of-task boundary" note-writing philosophy.
            try:
                note = run_summarizer(model, workspace, task, outcome)
                memory.record_note(session_id, str(workspace.path), note)
                append_journal_entry(workspace, task, note)
            except Exception:  # noqa: BLE001 - never let note-writing fail the turn
                pass

        registry.mark_done(turn_id, outcome)
    except Exception as exc:  # noqa: BLE001 - the turn must never crash the worker thread silently
        registry.mark_error(turn_id, str(exc))
