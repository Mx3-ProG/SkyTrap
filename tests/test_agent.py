from skytrap.core.agent import _build_system_prompt, _load_project_instructions
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
