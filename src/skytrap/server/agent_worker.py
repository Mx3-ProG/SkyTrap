from typing import Callable

from skytrap.core.agent import run_agent_turn
from skytrap.core.context import WorkspaceContext
from skytrap.memory.sqlite import SqliteMemory
from skytrap.models.base import ModelProvider
from skytrap.server.turns import TurnRegistry
from skytrap.server.ws.confirmation_bridge import ConfirmationBridge
from skytrap.tools.base import Tool
from skytrap.tools.filesystem import ListDirectoryTool, ReadFileTool, WriteFileTool
from skytrap.tools.git import GitDiffTool, GitStatusTool
from skytrap.tools.notes import GetPastNotesTool
from skytrap.tools.process import (
    ListBackgroundProcessesTool,
    StartBackgroundProcessTool,
    StopBackgroundProcessTool,
)
from skytrap.tools.search import SearchCodeTool
from skytrap.tools.shell import ShellTool
from skytrap.tools.tests import RunTestsTool


def build_server_toolset(bridge: ConfirmationBridge, memory: SqliteMemory | None = None) -> list[Tool]:
    """Same toolset as cli.py::_build_full_toolset, but every confirm callback
    calls bridge.request(preview, kind) instead of Rich's Confirm.ask() — routes
    the confirmation over the WebSocket instead of the terminal. Deliberately
    duplicated here (~15 lines) rather than shared with cli.py: two callers
    (Rich-terminal confirm vs. WebSocket-bridge confirm) don't justify a common
    abstraction yet, matching this project's stance against premature abstraction.
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
        WriteFileTool(confirm=lambda preview: bridge.request(preview, "write")),
        ShellTool(confirm=lambda preview: bridge.request(preview, "shell")),
        RunTestsTool(),
        StartBackgroundProcessTool(confirm=lambda preview: bridge.request(preview, "start_process")),
        ListBackgroundProcessesTool(),
        StopBackgroundProcessTool(confirm=lambda preview: bridge.request(preview, "stop_process")),
    ]
    if memory is not None:
        tools.append(GetPastNotesTool(memory=memory))
    return tools


def run_turn_in_background(
    turn_id: str,
    task: str,
    model: ModelProvider,
    tools: list[Tool],
    workspace: WorkspaceContext,
    registry: TurnRegistry,
    on_progress: Callable[[dict], None],
) -> None:
    """Entry point for the background thread spawned by POST /turns. Runs a single
    run_agent_turn call with a fresh history (each HTTP-triggered turn is
    independent — no multi-turn conversation state across separate POST /turns
    calls at this milestone) and records the outcome in the TurnRegistry so
    GET /turns/{id} reflects it.
    """
    registry.mark_running(turn_id)
    try:
        result = run_agent_turn(model, tools, workspace, [], task, on_step=on_progress)
    except Exception as exc:  # noqa: BLE001 - the turn must never crash the worker thread silently
        registry.mark_error(turn_id, str(exc))
        return
    registry.mark_done(turn_id, result)
