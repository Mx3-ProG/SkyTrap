"""Item 1 — UNIFY EXECUTION.

Proves there is exactly one execution policy: `run_agent_turn` (used by the
historical interactive `skytrap` chat, `skytrap build`'s Developer role, and the
web server) now routes every tool call through the same `ToolExecutor` —
RiskEngine + ApprovalEngine + inspect-before-write guard — that
`skytrap agent run`'s `AgentLoop` uses. In particular: the "announced creating
index.html" bug (tests/test_repository_intelligence.py) must be impossible via
the interactive path too, not just the autonomous one.
"""

import json
from pathlib import Path

from skytrap.autonomy.approval import ApprovalEngine
from skytrap.autonomy.executor import ToolExecutor
from skytrap.autonomy.memory import WorkingMemory
from skytrap.autonomy.risk import FULL_INTERACTIVE_CAPABILITIES, RiskEngine
from skytrap.autonomy.state import TaskState
from skytrap.core.agent import run_agent_turn
from skytrap.core.context import WorkspaceContext
from skytrap.core.roles import run_developer
from skytrap.models.base import ModelProvider
from skytrap.tools.filesystem import DeleteFileTool, ReadFileTool, WriteFileTool


def workspace(path: Path) -> WorkspaceContext:
    return WorkspaceContext(path=path, name=path.name, is_git=False)


class ScriptedModel(ModelProvider):
    name = "scripted"
    engine = "LOCAL"

    def __init__(self, responses: list[dict | str]):
        self.responses = responses
        self.calls = 0

    def chat(self, messages: list[dict]) -> str:
        response = self.responses[self.calls]
        self.calls += 1
        return response if isinstance(response, str) else json.dumps(response)


def _init_repo(root: Path) -> None:
    (root / "index.html").write_text(
        "<!doctype html>\n<html><head><script type=\"module\" src=\"/src/main.tsx\"></script>"
        "</head><body><div id=\"root\"></div></body></html>\n"
    )
    (root / "src").mkdir()
    (root / "src" / "main.tsx").write_text("console.log('entry');\n")


def _unified_executor(tools) -> ToolExecutor:
    """Exactly how `cli.py::_build_full_executor` and `core/roles.py::
    run_developer` build theirs — same classes, same capability set."""
    return ToolExecutor(
        tools,
        RiskEngine(),
        ApprovalEngine(),
        capabilities=FULL_INTERACTIVE_CAPABILITIES,
    )


def test_interactive_run_agent_turn_refuses_blind_overwrite_of_existing_index_html(tmp_path):
    _init_repo(tmp_path)
    ws = workspace(tmp_path)

    model = ScriptedModel(
        [
            # The exact reported bug: try to "create" index.html without reading it.
            {
                "type": "tool_call",
                "tool": "write_file",
                "arguments": {"path": "index.html", "content": "<html>brand new homepage</html>"},
            },
            {"type": "tool_call", "tool": "read_file", "arguments": {"path": "index.html"}},
            {
                "type": "tool_call",
                "tool": "write_file",
                "arguments": {"path": "index.html", "content": "<html>updated homepage</html>"},
            },
            {"type": "final", "message": "I created index.html with the new homepage."},
        ]
    )
    tools = [
        ReadFileTool(),
        WriteFileTool(confirm=lambda _: True),
        DeleteFileTool(confirm=lambda _: True),
    ]
    executor = _unified_executor(tools)
    task = TaskState(workspace_path=tmp_path, goal="Create the homepage")
    memory = WorkingMemory(objective="Create the homepage")

    reply = run_agent_turn(
        model,
        executor,
        task,
        memory,
        ws,
        history=[],
        user_input="Create the homepage",
        require_execution_evidence=True,
    )

    assert "updated homepage" in (tmp_path / "index.html").read_text()

    denied = [
        event for event in memory.events
        if event.kind == "tool_result" and event.data.get("tool") == "write_file" and not event.data.get("success")
    ]
    assert denied, "interactive mode must refuse the blind overwrite exactly like autonomous mode does"
    assert denied[0].data.get("status") == "denied"

    successful_writes = [
        event for event in memory.events
        if event.kind == "tool_result" and event.data.get("tool") == "write_file" and event.data.get("success")
    ]
    assert len(successful_writes) == 1
    assert successful_writes[0].data.get("is_new_file") is False
    assert reply  # the model still reached a final answer after self-correcting


def test_interactive_session_memory_persists_the_read_across_turns(tmp_path):
    """The write guard's "have I looked at this file in this task" memory must
    carry across separate user turns within one interactive session — not reset
    on every message — the same way it persists across iterations within a
    single autonomous task."""
    _init_repo(tmp_path)
    ws = workspace(tmp_path)
    tools = [ReadFileTool(), WriteFileTool(confirm=lambda _: True)]
    executor = _unified_executor(tools)
    task = TaskState(workspace_path=tmp_path, goal="session")
    memory = WorkingMemory(objective="session")

    # Turn 1: just reads the file.
    read_model = ScriptedModel(
        [
            {"type": "tool_call", "tool": "read_file", "arguments": {"path": "index.html"}},
            {"type": "final", "message": "Here's what index.html contains."},
        ]
    )
    run_agent_turn(read_model, executor, task, memory, ws, history=[], user_input="Show me index.html")

    # Turn 2 (separate call, same task/memory): writes to the same file without
    # re-reading it in this turn — must be allowed, because it was read in turn 1.
    write_model = ScriptedModel(
        [
            {
                "type": "tool_call",
                "tool": "write_file",
                "arguments": {"path": "index.html", "content": "<html>turn two</html>"},
            },
            {"type": "final", "message": "Updated it."},
        ]
    )
    run_agent_turn(write_model, executor, task, memory, ws, history=[], user_input="Now change the title")

    assert "turn two" in (tmp_path / "index.html").read_text()
    denied = [
        event for event in memory.events
        if event.kind == "tool_result" and event.data.get("tool") == "write_file" and not event.data.get("success")
    ]
    assert not denied


def test_run_developer_role_shares_the_same_guard(tmp_path):
    """`skytrap build`'s Developer role (core.roles.run_developer) goes through
    the identical ToolExecutor construction — not a separate policy."""
    _init_repo(tmp_path)
    ws = workspace(tmp_path)
    model = ScriptedModel(
        [
            {
                "type": "tool_call",
                "tool": "write_file",
                "arguments": {"path": "index.html", "content": "<html>blind</html>"},
            },
            {"type": "tool_call", "tool": "read_file", "arguments": {"path": "index.html"}},
            {
                "type": "tool_call",
                "tool": "write_file",
                "arguments": {"path": "index.html", "content": "<html>informed</html>"},
            },
            {"type": "final", "message": "Updated the homepage."},
        ]
    )
    tools = [ReadFileTool(), WriteFileTool(confirm=lambda _: True)]

    run_developer(
        model,
        tools,
        ws,
        "Create the homepage",
        "Plan: update index.html",
        approval_callback=lambda request: True,
    )

    assert "informed" in (tmp_path / "index.html").read_text()
