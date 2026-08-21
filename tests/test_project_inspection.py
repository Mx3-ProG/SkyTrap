from skytrap.core.context import WorkspaceContext
from skytrap.core.project_inspection import inspect_project, resolve_commands
from skytrap.tools.project import InspectProjectTool


def _workspace(tmp_path):
    return WorkspaceContext(path=tmp_path, name=tmp_path.name, is_git=False)


def test_inspect_project_rust_reports_primary_language_and_commands(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'x'\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.rs").write_text("fn main() {}\n")

    profile = inspect_project(_workspace(tmp_path))

    assert profile.primary_language.profile.id == "rust"
    commands = resolve_commands(_workspace(tmp_path), profile.primary_language)
    assert commands.check_command == "cargo check"
    assert "cargo test" in commands.test_commands


def test_inspect_project_empty_workspace_has_no_languages(tmp_path):
    profile = inspect_project(_workspace(tmp_path))
    assert profile.languages == []
    assert profile.primary_language is None


def test_javascript_resolve_commands_picks_pnpm_from_lockfile(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "x", "scripts": {"test": "vitest"}}')
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")

    profile = inspect_project(_workspace(tmp_path))
    match = next(m for m in profile.languages if m.profile.id == "javascript")
    commands = resolve_commands(_workspace(tmp_path), match)

    assert commands.test_commands == ("pnpm test",)
    assert commands.build_commands == ("pnpm build",)


def test_inspect_project_tool_reports_detected_language(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'x'\n")

    result = InspectProjectTool().execute(_workspace(tmp_path), {})

    assert result.success is True
    assert "Rust" in result.output
    assert "cargo check" in result.output
