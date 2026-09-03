import json

from skytrap.autonomy.approval import ApprovalEngine
from skytrap.autonomy.executor import ToolExecutor
from skytrap.autonomy.memory import WorkingMemory
from skytrap.autonomy.risk import Capability, RiskEngine
from skytrap.autonomy.state import TaskState
from skytrap.core.agent import _build_system_prompt, _load_project_instructions, _parse_decision, run_agent_turn
from skytrap.core.context import WorkspaceContext
from skytrap.core.project_notes import append_journal_entry
from skytrap.models.base import ModelProvider
from skytrap.tools.base import Tool, ToolResult


def _workspace(tmp_path):
    return WorkspaceContext(path=tmp_path, name=tmp_path.name, is_git=False)


def _turn(tmp_path, tools=()):
    """Item 1 — run_agent_turn now always routes through a real ToolExecutor
    (RiskEngine + ApprovalEngine), the same one skytrap agent run uses. This
    builds a fresh executor/task/memory triple for a single test call."""
    workspace = _workspace(tmp_path)
    executor = ToolExecutor(
        list(tools),
        RiskEngine(),
        ApprovalEngine(),
        capabilities={Capability.FILESYSTEM_READ, Capability.FILESYSTEM_WRITE, Capability.SHELL_EXECUTE},
    )
    task = TaskState(workspace_path=workspace.path, goal="test")
    memory = WorkingMemory(objective="test")
    return executor, task, memory, workspace


class _CountingModel(ModelProvider):
    """Never gives a final answer — used to exercise the max_steps cutoff."""

    name = "counting"
    engine = "LOCAL"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: list[dict]) -> str:
        self.calls += 1
        return json.dumps({"type": "tool_call", "tool": "nonexistent", "arguments": {}})


def test_load_project_instructions_absent(tmp_path):
    assert _load_project_instructions(_workspace(tmp_path)) is None


def test_load_project_instructions_present(tmp_path):
    (tmp_path / "SKYTRAP.md").write_text("Custom rule.")
    assert _load_project_instructions(_workspace(tmp_path)) == "Custom rule."


def test_system_prompt_includes_project_instructions(tmp_path):
    (tmp_path / "SKYTRAP.md").write_text("Custom rule.")
    prompt = _build_system_prompt(_workspace(tmp_path), [])
    assert "Custom rule." in prompt
    assert "MANDATORY PROJECT RULES" in prompt


def test_system_prompt_omits_section_when_no_instructions_file(tmp_path):
    prompt = _build_system_prompt(_workspace(tmp_path), [])
    assert "MANDATORY PROJECT RULES" not in prompt


def test_system_prompt_includes_journal_continuity_notes(tmp_path):
    workspace = _workspace(tmp_path)
    append_journal_entry(workspace, "Previous task", "Implemented the previous task in foo.py.")

    prompt = _build_system_prompt(workspace, [])
    assert "CONTINUITY NOTES" in prompt
    assert "Implemented the previous task in foo.py." in prompt


def test_system_prompt_omits_continuity_notes_when_no_journal(tmp_path):
    prompt = _build_system_prompt(_workspace(tmp_path), [])
    assert "CONTINUITY NOTES" not in prompt


def test_parse_decision_valid_json():
    decision = _parse_decision('{"type": "final", "message": "hi"}')
    assert decision.type == "final"
    assert decision.message == "hi"


def test_parse_decision_tool_call():
    decision = _parse_decision(
        '{"type": "tool_call", "tool": "read_file", "arguments": {"path": "a.py"}}'
    )
    assert decision.type == "tool_call"
    assert decision.tool == "read_file"
    assert decision.arguments == {"path": "a.py"}


def test_parse_decision_tolerates_literal_newlines_in_message():
    # Real models often emit multi-line "message" values with literal newline bytes
    # instead of an escaped \n, which strict JSON rejects.
    raw = '{"type": "final", "message": "line one\nline two"}'
    decision = _parse_decision(raw)
    assert decision.type == "final"
    assert decision.message == "line one\nline two"


def test_parse_decision_falls_back_to_raw_text_on_garbage():
    decision = _parse_decision("not json at all")
    assert decision.type == "final"
    assert decision.message == "not json at all"


def test_run_agent_turn_respects_custom_max_steps(tmp_path):
    model = _CountingModel()
    executor, task, memory, workspace = _turn(tmp_path)
    run_agent_turn(model, executor, task, memory, workspace, [], "do something", max_steps=3)
    assert model.calls == 3


def test_run_agent_turn_defaults_to_five_steps(tmp_path):
    model = _CountingModel()
    executor, task, memory, workspace = _turn(tmp_path)
    run_agent_turn(model, executor, task, memory, workspace, [], "do something")
    assert model.calls == 5


class _ScriptedModel(ModelProvider):
    """Replays a fixed sequence of raw responses, one per call."""

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


def _final(message: str) -> str:
    return json.dumps({"type": "final", "message": message})


def test_execution_guard_rejects_hedge_and_accepts_after_real_write(tmp_path):
    model = _ScriptedModel(
        [
            _final("I cannot create an entire project, but I can show you how."),
            json.dumps({"type": "tool_call", "tool": "write_file", "arguments": {"path": "a.py", "content": "x"}}),
            _final("Created a.py."),
        ]
    )
    executor, task, memory, workspace = _turn(tmp_path, tools=[_FakeWriteFileTool()])
    result = run_agent_turn(
        model,
        executor,
        task,
        memory,
        workspace,
        [],
        "Programme ce projet.",
        max_steps=10,
        require_execution_evidence=True,
    )
    assert result == "Created a.py."
    assert model.calls == 3


def test_execution_guard_gives_up_after_max_rejections(tmp_path):
    model = _ScriptedModel(
        [
            _final("I cannot create an entire project, but I can show you how."),
            _final("I can show you how, but I cannot build it myself."),
            _final("I can show you how, but I cannot build it myself."),
        ]
    )
    executor, task, memory, workspace = _turn(tmp_path)
    result = run_agent_turn(
        model,
        executor,
        task,
        memory,
        workspace,
        [],
        "Programme ce projet.",
        max_steps=10,
        require_execution_evidence=True,
    )
    # Accepted on the 3rd attempt: 2 rejections consumed, cap reached.
    assert result == "I can show you how, but I cannot build it myself."
    assert model.calls == 3


def test_execution_guard_accepts_legitimate_zero_tool_answer(tmp_path):
    # A tool-free final that isn't a hedge (e.g. "nothing to change") must be
    # accepted immediately — the guard targets hedging, not the absence of a tool
    # call by itself.
    model = _ScriptedModel([_final("No changes needed — this can be answered directly.")])
    executor, task, memory, workspace = _turn(tmp_path)
    result = run_agent_turn(
        model,
        executor,
        task,
        memory,
        workspace,
        [],
        "Programme ce projet.",
        max_steps=10,
        require_execution_evidence=True,
    )
    assert result == "No changes needed — this can be answered directly."
    assert model.calls == 1


def test_execution_guard_inactive_when_not_required(tmp_path):
    model = _ScriptedModel([_final("I cannot create an entire project, but I can show you how.")])
    executor, task, memory, workspace = _turn(tmp_path)
    result = run_agent_turn(
        model, executor, task, memory, workspace, [], "Explique-moi ce fichier.", max_steps=10
    )
    assert result == "I cannot create an entire project, but I can show you how."
    assert model.calls == 1


def test_parse_decision_repairs_unescaped_quotes_in_final_message():
    # A real qwen2.5-coder:7b response: the message quotes a code snippet with raw,
    # unescaped double quotes, which breaks JSON string boundaries entirely (not just
    # a control-character issue json.loads(strict=False) can tolerate).
    raw = (
        '{\n  "type": "final",\n  "message": "Add a class that takes {"path": ...} '
        'as an argument."\n}'
    )
    decision = _parse_decision(raw)
    assert decision.type == "final"
    assert decision.message == 'Add a class that takes {"path": ...} as an argument.'
