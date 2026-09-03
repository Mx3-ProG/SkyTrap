"""Unit-level coverage for the Repository/Code Intelligence layer
(skytrap.intelligence.*) — separate from tests/test_repository_intelligence.py,
which reproduces the specific reported bug end-to-end through AgentLoop.
"""

import sqlite3
import subprocess
from pathlib import Path

from skytrap.core.context import WorkspaceContext
from skytrap.core.doctor import DEGRADED, HEALTHY, UNAVAILABLE, run_doctor
from skytrap.intelligence.context_builder import ContextBuilder
from skytrap.intelligence.existence import ExistenceStatus, check_existence
from skytrap.intelligence.graph import DependencyGraph
from skytrap.intelligence.lsp import LanguageIntelligenceProvider
from skytrap.intelligence.parser import CodeParser
from skytrap.intelligence.repository_memory import RepositoryMemory, RepositoryMemoryStore
from skytrap.intelligence.snapshot import build_repository_snapshot
from skytrap.intelligence.structural_search import StructuralSearch
from skytrap.intelligence.symbols import SymbolIndex
from skytrap.tools.structural_search import StructuralSearchTool


def ws(path: Path) -> WorkspaceContext:
    return WorkspaceContext(path=path, name=path.name, is_git=False)


# -- CodeParser ---------------------------------------------------------------


def test_code_parser_extracts_python_symbols_imports_and_calls():
    parser = CodeParser()
    assert parser.available()
    src = "import os\n\nclass Foo:\n    def bar(self, x):\n        return os.path.join(x)\n\ndef top():\n    return Foo()\n"
    parsed = parser.parse_source(src, "python", path="a.py")
    kinds = {(s.name, s.kind) for s in parsed.symbols}
    assert ("Foo", "class") in kinds
    assert ("bar", "method") in kinds
    assert ("top", "function") in kinds
    assert any("import os" in i for i in parsed.imports)
    assert "Foo" in parsed.calls


def test_code_parser_extracts_javascript_functions_classes_and_components():
    parser = CodeParser()
    src = (
        "import React from 'react';\n"
        "export function useThing() { return call(1); }\n"
        "const LoginPage = () => { return null; };\n"
        "class Store { method() {} }\n"
    )
    parsed = parser.parse_source(src, "javascript", path="a.js")
    kinds = {(s.name, s.kind) for s in parsed.symbols}
    assert ("useThing", "function") in kinds
    assert ("LoginPage", "component") in kinds
    assert ("Store", "class") in kinds
    assert ("method", "method") in kinds
    assert any("import React" in i for i in parsed.imports)
    assert "call" in parsed.calls


def test_code_parser_extracts_html_entrypoint_script_reference():
    parser = CodeParser()
    src = '<html><head><script type="module" src="/src/main.tsx"></script></head><body></body></html>'
    parsed = parser.parse_source(src, "html", path="index.html")
    assert "/src/main.tsx" in parsed.imports


def test_code_parser_extracts_css_selectors():
    parser = CodeParser()
    src = ".btn { color: red; }\n#root { display: flex; }\n"
    parsed = parser.parse_source(src, "css", path="a.css")
    names = {s.name for s in parsed.symbols}
    assert any(".btn" in n for n in names)
    assert any("#root" in n for n in names)


def test_code_parser_unsupported_extension_returns_none(tmp_path):
    parser = CodeParser()
    file = tmp_path / "data.bin"
    file.write_bytes(b"\x00\x01")
    assert parser.parse_file(file, relative_path="data.bin") is None


# -- SymbolIndex / DependencyGraph --------------------------------------------


def _write_ts_project(root: Path) -> None:
    (root / "src").mkdir()
    (root / "src" / "App.tsx").write_text(
        "import React from 'react';\nimport { AuthProvider } from './auth/AuthProvider';\n\n"
        "export default function App() {\n  return <AuthProvider><div/></AuthProvider>;\n}\n"
    )
    (root / "src" / "auth").mkdir()
    (root / "src" / "auth" / "AuthProvider.tsx").write_text(
        "import React from 'react';\nimport { createSession } from './session';\n\n"
        "export function AuthProvider({ children }) {\n  createSession();\n  return children;\n}\n"
    )
    (root / "src" / "auth" / "session.ts").write_text(
        "export function createSession() {\n  return { token: 'x' };\n}\n"
    )


def test_symbol_index_builds_incrementally_and_updates_on_change(tmp_path):
    _write_ts_project(tmp_path)
    workspace = ws(tmp_path)
    index = SymbolIndex().build(workspace)

    assert index.find("AuthProvider")
    assert index.find("createSession")[0].file == "src/auth/session.ts"

    (tmp_path / "src" / "auth" / "session.ts").write_text(
        "export function createSession() {\n  return { token: 'x' };\n}\n\nexport function destroySession() {}\n"
    )
    index.update_file(workspace, "src/auth/session.ts")
    assert index.find("destroySession")

    index.remove_file("src/auth/session.ts")
    assert index.find("createSession") == []


def test_dependency_graph_resolves_relative_imports_and_impact(tmp_path):
    _write_ts_project(tmp_path)
    workspace = ws(tmp_path)
    snapshot = build_repository_snapshot(workspace)
    index = SymbolIndex().build(workspace)
    graph = DependencyGraph().build(index, snapshot.files)

    assert "src/auth/AuthProvider.tsx" in graph.dependencies_of("src/App.tsx")
    assert "src/App.tsx" in graph.impacted_by("src/auth/session.ts")
    assert "src/auth/AuthProvider.tsx" in graph.impacted_by("src/auth/session.ts")


# -- ContextBuilder ------------------------------------------------------------


def test_context_builder_never_injects_the_whole_repository_and_respects_budget(tmp_path):
    _write_ts_project(tmp_path)
    workspace = ws(tmp_path)
    snapshot = build_repository_snapshot(workspace)
    index = SymbolIndex().build(workspace)
    graph = DependencyGraph().build(index, snapshot.files)

    built = ContextBuilder().build(
        workspace,
        goal="Add authentication",
        snapshot=snapshot,
        symbol_index=index,
        dependency_graph=graph,
        token_budget=40,  # deliberately tiny — must drop low-priority sections
    )
    assert built.estimated_tokens <= 40 + 5  # the always-kept Request section can slightly exceed
    assert built.dropped_sections  # something had to give under such a tight budget
    rendered = built.render()
    # never the raw content of every file wholesale
    assert rendered.count("createSession") <= 2


def test_context_builder_prioritizes_request_section_even_under_tiny_budget(tmp_path):
    _write_ts_project(tmp_path)
    workspace = ws(tmp_path)
    snapshot = build_repository_snapshot(workspace)
    built = ContextBuilder().build(workspace, goal="Fix the login bug", snapshot=snapshot, token_budget=1)
    assert any(s.title == "Request" for s in built.sections)


# -- Existence check (UNKNOWN vs MISSING) -------------------------------------


def test_existence_unknown_when_ripgrep_unavailable(tmp_path, monkeypatch):
    _write_ts_project(tmp_path)
    workspace = ws(tmp_path)
    snapshot = build_repository_snapshot(workspace)

    import skytrap.intelligence.existence as existence_module

    monkeypatch.setattr(existence_module, "_ripgrep_available", lambda: False)
    evidence = check_existence(workspace, snapshot, "payment gateway")
    assert evidence.status == ExistenceStatus.UNKNOWN


def test_existence_missing_when_truly_absent(tmp_path):
    _write_ts_project(tmp_path)
    workspace = ws(tmp_path)
    snapshot = build_repository_snapshot(workspace)
    evidence = check_existence(workspace, snapshot, "payment gateway")
    assert evidence.status == ExistenceStatus.MISSING


# -- RepositoryMemory ----------------------------------------------------------


def test_repository_memory_round_trips_and_detects_stale_fingerprint(tmp_path):
    connection = sqlite3.connect(":memory:")
    store = RepositoryMemoryStore(connection)
    memory = RepositoryMemory(workspace_path="/repo", fingerprint="abc123", architecture="Python")
    store.save(memory)

    loaded = store.load("/repo")
    assert loaded is not None
    assert loaded.architecture == "Python"

    assert store.load_if_current("/repo", "abc123") is not None
    assert store.load_if_current("/repo", "different-fingerprint") is None


# -- StructuralSearch / tool ----------------------------------------------------


def test_structural_search_finds_the_real_call_site(tmp_path):
    # Item 8 — this exercises whichever backend is actually available in this
    # environment: real ast-grep when installed (precise — only true call
    # sites, not the declaration), the tree-sitter+ripgrep fallback otherwise
    # (looser — also surfaces the declaration via the symbol index).
    _write_ts_project(tmp_path)
    workspace = ws(tmp_path)
    index = SymbolIndex().build(workspace)
    search = StructuralSearch(symbol_index=index)

    matches = search.find_calls(workspace, "createSession")
    files = {m.file for m in matches}
    assert "src/auth/AuthProvider.tsx" in files  # the real call site, found by either backend

    if search.backend() == "ast-grep":
        assert not any(m.approximate for m in matches)
    else:
        assert "src/auth/session.ts" in files  # fallback also surfaces the declaration
        assert all(m.approximate for m in matches)


def test_structural_search_tool_requires_pattern(tmp_path):
    tool = StructuralSearchTool()
    result = tool.execute(ws(tmp_path), {})
    assert result.success is False


def test_structural_search_tool_reports_backend_metadata(tmp_path):
    _write_ts_project(tmp_path)
    tool = StructuralSearchTool()
    result = tool.execute(ws(tmp_path), {"pattern": "createSession($$$ARGS)"})
    assert result.success is True
    assert "backend" in result.metadata


# -- LSP provider (honest detection-only contract) -----------------------------


def test_lsp_provider_detection_is_real_and_calls_require_context():
    provider = LanguageIntelligenceProvider()
    detected = provider.detect()
    assert isinstance(detected, dict)
    result = provider.definition()
    assert result.supported is False
    assert "requires workspace and path" in result.detail


# -- doctor ---------------------------------------------------------------------


def test_doctor_reports_a_check_for_every_dependency_and_a_real_git_status(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    workspace = ws(tmp_path)
    workspace = workspace.model_copy(update={"is_git": True, "branch": "main"})

    report = run_doctor(workspace)
    names = {c.name for c in report.checks}
    assert {"Git", "ripgrep", "Tree-sitter", "Task state storage", "Workspace permissions"} <= names
    assert report.overall in {HEALTHY, DEGRADED, UNAVAILABLE}


def test_doctor_flags_missing_workspace_permissions(tmp_path, monkeypatch):
    workspace = ws(tmp_path)

    import skytrap.core.doctor as doctor_module

    def boom(*_args, **_kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "write_text", boom)
    check = doctor_module._check_workspace_permissions(workspace)
    assert check.status == UNAVAILABLE
