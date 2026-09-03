"""Item 6 — VERIFICATION FALLBACK ENGINE.

Reproduces the observed "no verification command discovered" failure on a
Vite/TypeScript project with no npm "scripts" entries, and proves a known,
gated fallback registry (tsc --noEmit, vite build, pytest, ruff, mypy) fills
the gap — never an arbitrary/dangerous command, always still checked for real
tool presence via the existing _command_is_configured gate.
"""

import json
from pathlib import Path

from skytrap.autonomy.verification import VerificationLoop, VerificationStage
from skytrap.core.context import WorkspaceContext


def workspace(path: Path) -> WorkspaceContext:
    return WorkspaceContext(path=path, name=path.name, is_git=False)


def test_vite_project_without_npm_scripts_gets_no_primary_commands(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "demo", "dependencies": {"react": "^18.0.0"}, "devDependencies": {"vite": "^5.0.0"}})
    )
    (tmp_path / "vite.config.js").write_text("export default {};\n")
    loop = VerificationLoop()
    discovered = loop.discover(workspace(tmp_path))
    assert not any(discovered.values()), "package.json has no scripts — primary discovery should find nothing"


def test_fallback_proposes_vite_build_when_vite_is_actually_resolvable(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "demo", "dependencies": {}, "devDependencies": {"vite": "^5.0.0"}})
    )
    (tmp_path / "vite.config.js").write_text("export default {};\n")
    (tmp_path / "node_modules" / ".bin").mkdir(parents=True)
    (tmp_path / "node_modules" / ".bin" / "vite").write_text("#!/bin/sh\necho vite\n")

    loop = VerificationLoop()
    fallback = loop._discover_fallback(workspace(tmp_path))
    assert "vite build" in fallback[VerificationStage.BUILD]


def test_fallback_never_proposes_a_tool_that_is_not_actually_present(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "demo", "dependencies": {}, "devDependencies": {"vite": "^5.0.0"}})
    )
    (tmp_path / "vite.config.js").write_text("export default {};\n")
    # No node_modules/.bin/vite and (almost certainly) no global vite binary —
    # the fallback must not hand back a command that can't actually run.
    loop = VerificationLoop()
    fallback = loop._discover_fallback(workspace(tmp_path))
    assert fallback[VerificationStage.BUILD] == []


def test_fallback_proposes_pytest_for_a_python_project_with_tests(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_ok():\n    assert True\n")
    loop = VerificationLoop()
    fallback = loop._discover_fallback(workspace(tmp_path))
    # pytest is a real dev dependency of this project, so it's genuinely on PATH
    # in this test environment — this is a real gate, not a mocked one.
    assert "pytest" in fallback[VerificationStage.TEST]


def test_fallback_proposes_ruff_only_when_ruff_config_present_and_python_files_exist(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n")
    (tmp_path / "ruff.toml").write_text("line-length = 100\n")
    loop = VerificationLoop()
    fallback = loop._discover_fallback(workspace(tmp_path))
    if any((tmp_path).rglob("*.py")):
        # Only asserted if ruff is actually resolvable in this environment —
        # the point is it's never proposed blindly when it isn't.
        import shutil

        if shutil.which("ruff"):
            assert "ruff check ." in fallback[VerificationStage.LINT]
        else:
            assert fallback[VerificationStage.LINT] == []


def test_discover_finds_a_real_test_command_for_a_python_project_with_tests(tmp_path):
    # The language-profile default (python3 -m unittest) already covers this
    # case via primary discovery — the fallback engine only kicks in when
    # *nothing at all* was found (the Vite-without-scripts case above).
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_ok():\n    assert True\n")
    loop = VerificationLoop()
    discovered = loop.discover(workspace(tmp_path))
    assert discovered[VerificationStage.TEST], "some real test command must be discovered when tests exist"


def test_discover_falls_back_end_to_end_for_a_vite_project_with_no_npm_scripts(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "demo", "dependencies": {}, "devDependencies": {"vite": "^5.0.0"}})
    )
    (tmp_path / "vite.config.js").write_text("export default {};\n")
    (tmp_path / "node_modules" / ".bin").mkdir(parents=True)
    (tmp_path / "node_modules" / ".bin" / "vite").write_text("#!/bin/sh\necho vite\n")

    loop = VerificationLoop()
    discovered = loop.discover(workspace(tmp_path))
    assert "vite build" in discovered[VerificationStage.BUILD]
