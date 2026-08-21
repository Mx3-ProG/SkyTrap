import json

from skytrap.core.context import WorkspaceContext
from skytrap.core.roles import _looks_like_refusal, run_developer
from skytrap.models.base import ModelProvider
from skytrap.tools.base import Tool, ToolResult


def test_detects_refusal_phrases():
    assert _looks_like_refusal("The 'write_file' tool is not available in the workspace.")
    assert _looks_like_refusal("I apologize for the confusion.")
    assert _looks_like_refusal("I cannot do this task.")


def test_does_not_flag_a_real_plan():
    plan = "1. In src/skytrap/tools/git.py, add a new function.\n2. Add a test."
    assert not _looks_like_refusal(plan)


class _ScriptedModel(ModelProvider):
    name = "scripted"
    engine = "LOCAL"

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def chat(self, messages: list[dict]) -> str:
        raw = self._responses[self.calls]
        self.calls += 1
        return raw


class _FakeWriteFileTool(Tool):
    name = "write_file"
    description = "test double"

    def execute(self, workspace, arguments) -> ToolResult:
        return ToolResult(success=True, output="wrote it")


def test_run_developer_rejects_a_hedge_and_implements_after_the_nudge(tmp_path):
    # run_developer always passes require_execution_evidence=True to run_agent_turn
    # — a Developer-role hedge ("I can show you how") must be rejected and the loop
    # must continue until a real write_file call happens.
    model = _ScriptedModel(
        [
            json.dumps({"type": "final", "message": "I can show you how, but I cannot build it myself."}),
            json.dumps(
                {"type": "tool_call", "tool": "write_file", "arguments": {"path": "a.py", "content": "x"}}
            ),
            json.dumps({"type": "final", "message": "Created a.py."}),
        ]
    )
    workspace = WorkspaceContext(path=tmp_path, name=tmp_path.name, is_git=False)

    result = run_developer(model, [_FakeWriteFileTool()], workspace, "Programme ce projet.", "1. Create a.py.")

    assert result == "Created a.py."
    assert model.calls == 3
