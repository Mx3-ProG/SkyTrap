from skytrap.core.agent import _build_system_prompt, _load_project_instructions, _parse_decision
from skytrap.core.context import WorkspaceContext


def _workspace(tmp_path):
    return WorkspaceContext(path=tmp_path, name=tmp_path.name, is_git=False)


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
