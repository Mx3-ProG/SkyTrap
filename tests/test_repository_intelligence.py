"""Item 18 — integration test reproducing the real observed bug: SkyTrap
announcing it "created index.html" in a repository where index.html (and a
Vite/React app) already existed, plus the companion "Add authentication"
scenario where auth is only PARTIALLY implemented.

These tests exercise the full pipeline: HumanIntentEngine -> RepositoryDiscovery
(RepositorySnapshot + existence checks) -> Planner -> AgentLoop -> ToolExecutor's
inspect-before-write guard -> the claim-validated final report.
"""

import json
import subprocess
from pathlib import Path

from skytrap.autonomy import (
    ApprovalEngine,
    Capability,
    Planner,
    RiskEngine,
    TaskState,
    TaskStatus,
    TaskStore,
    ToolExecutor,
    VerificationLoop,
    VerificationStage,
    WorkingMemory,
)
from skytrap.autonomy.loop import AgentLoop, _verified_summary
from skytrap.autonomy.planning import FileActionType, PlanFileAction, TaskPlan
from skytrap.core.context import WorkspaceContext
from skytrap.intelligence.existence import ExistenceEvidence, ExistenceStatus, check_existence
from skytrap.intelligence.snapshot import build_repository_snapshot
from skytrap.intelligence.symbols import SymbolIndex
from skytrap.models.base import ModelProvider
from skytrap.tools.base import ToolResult
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


def _init_vite_react_repo(root: Path) -> None:
    (root / "index.html").write_text(
        "<!doctype html>\n<html><head><script type=\"module\" src=\"/src/main.tsx\"></script>"
        "</head><body><div id=\"root\"></div></body></html>\n"
    )
    (root / "package.json").write_text(
        json.dumps({"name": "demo", "dependencies": {"react": "^18.0.0", "react-dom": "^18.0.0"}, "devDependencies": {"vite": "^5.0.0"}})
    )
    (root / "vite.config.js").write_text("export default {};\n")
    (root / "src").mkdir()
    (root / "src" / "main.tsx").write_text(
        "import { createRoot } from 'react-dom/client';\nimport App from './App';\n\n"
        "createRoot(document.getElementById('root')!).render(<App />);\n"
    )
    (root / "src" / "App.tsx").write_text(
        "export default function App() {\n  return <div>hello</div>;\n}\n"
    )
    (root / "src" / "auth").mkdir()
    (root / "src" / "auth" / "session.ts").write_text(
        "export function createSession() {\n  return { token: 'partial' };\n}\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", "init"],
        cwd=root,
        check=True,
    )


class TrivialPassVerifier(VerificationLoop):
    """No lint/typecheck/test/build tools registered on the executor, so
    AgentLoop falls back to VerificationLoop.discover(). A single
    always-succeeding command is enough to prove *something* was verified —
    these tests are about discovery/planning/write-guard behavior, not CI."""

    def discover(self, workspace):
        return {
            VerificationStage.LINT: [],
            VerificationStage.TYPECHECK: [],
            VerificationStage.TEST: ["python3 -c 'pass'"],
            VerificationStage.BUILD: [],
        }


def _build_loop(model, tmp_path: Path) -> tuple[AgentLoop, ToolExecutor]:
    executor = ToolExecutor(
        [ReadFileTool(), WriteFileTool(confirm=lambda _: True), DeleteFileTool(confirm=lambda _: True)],
        RiskEngine(),
        ApprovalEngine(),
        capabilities={Capability.FILESYSTEM_READ, Capability.FILESYSTEM_WRITE},
    )
    store = TaskStore(tmp_path / ".state")
    loop = AgentLoop(model, Planner(model), executor, TrivialPassVerifier(), store)
    return loop, executor


def test_repository_snapshot_reports_index_html_as_existing(tmp_path):
    _init_vite_react_repo(tmp_path)
    snapshot = build_repository_snapshot(workspace(tmp_path))

    assert "index.html" in snapshot.files
    assert "index.html" in snapshot.entrypoints
    assert "React" in snapshot.frameworks

    evidence = check_existence(workspace(tmp_path), snapshot, "index.html")
    assert evidence.status == ExistenceStatus.EXISTS


def test_existence_check_reports_partial_for_incomplete_authentication(tmp_path):
    _init_vite_react_repo(tmp_path)
    snapshot = build_repository_snapshot(workspace(tmp_path))

    evidence = check_existence(workspace(tmp_path), snapshot, "authentication")

    assert evidence.status == ExistenceStatus.PARTIAL
    assert any("auth" in f for f in evidence.matched_files)


def test_planner_downgrades_create_to_modify_when_evidence_says_exists(tmp_path):
    _init_vite_react_repo(tmp_path)
    ws = workspace(tmp_path)
    snapshot = build_repository_snapshot(ws)

    # Simulate a model that made exactly the observed mistake: proposing to
    # "create" index.html despite it already existing.
    naive_plan = TaskPlan(
        summary="Build the homepage",
        steps=[],
        files=["index.html"],
        file_actions=[PlanFileAction(path="index.html", action=FileActionType.CREATE, justification="need a homepage")],
    )
    evidence = [check_existence(ws, snapshot, "index.html")]

    from skytrap.autonomy.planning import _reconcile_file_actions

    reconciled = _reconcile_file_actions(naive_plan, evidence)

    assert reconciled.file_actions[0].action == FileActionType.MODIFY
    assert "already exists" in reconciled.file_actions[0].justification


def test_agent_loop_never_claims_created_for_a_pre_existing_file_and_enforces_read_before_write(tmp_path):
    _init_vite_react_repo(tmp_path)
    ws = workspace(tmp_path)

    model = ScriptedModel(
        [
            # Planner call — deterministic fallback plan will be used if this
            # isn't valid JSON; a minimal valid plan naming index.html is enough.
            {
                "summary": "Build the homepage",
                "steps": [{"id": "step-1", "description": "Update the homepage", "files": ["index.html"], "commands": [], "risks": [], "success_criteria": ["homepage updated"]}],
                "files": ["index.html"],
                "file_actions": [{"path": "index.html", "action": "modify", "justification": "index.html already exists"}],
                "tests": [],
                "commands": [],
                "risks": [],
                "success_criteria": ["homepage updated"],
            },
            # Buggy first instinct: blindly "create"/overwrite index.html
            # without reading it first — this must be refused.
            {
                "type": "tool_call",
                "tool": "write_file",
                "arguments": {"path": "index.html", "content": "<html>brand new homepage</html>"},
            },
            # After the refusal, the model does the right thing: read first.
            {"type": "tool_call", "tool": "read_file", "arguments": {"path": "index.html"}},
            # Now the write is allowed — this is a real modification, not a creation.
            {
                "type": "tool_call",
                "tool": "write_file",
                "arguments": {"path": "index.html", "content": "<html>updated homepage</html>"},
            },
            {"type": "final", "message": "I created index.html with the new homepage."},
        ]
    )
    loop, executor = _build_loop(model, tmp_path)
    task = TaskState(workspace_path=tmp_path, goal="Create the homepage", max_iterations=10)

    completed = loop.run(ws, task)

    assert completed.status == TaskStatus.COMPLETED
    _, memory = loop.store.load(task.task_id)

    # The naive write_file call was denied before ever touching the file.
    denial_events = [
        e for e in memory.events
        if e.kind == "tool_result" and e.data.get("tool") == "write_file" and not e.data.get("success")
    ]
    assert denial_events, "the blind overwrite of an existing file should have been refused"
    assert denial_events[0].data.get("status") == "denied"

    successful_writes = [
        e for e in memory.events
        if e.kind == "tool_result" and e.data.get("tool") == "write_file" and e.data.get("success")
    ]
    assert len(successful_writes) == 1, "only the post-read write should have succeeded"
    # The successful write happened on an existing file, so it's a modification.
    assert successful_writes[0].data.get("is_new_file") is False

    # index.html still exists and was actually modified (not deleted/recreated).
    assert (tmp_path / "index.html").exists()
    assert "updated homepage" in (tmp_path / "index.html").read_text()

    # Item 16 — claim validation: the model said "I created index.html" but the
    # real events show it was modified, not created. The final report must not
    # repeat that false claim uncorrected.
    summary = _verified_summary(memory)
    assert "index.html" in summary["modified_files"]
    assert "index.html" not in summary["created_files"]
    assert "already existed and was modified, not created" in completed.final_message


def test_agent_loop_reuses_partial_auth_instead_of_duplicating(tmp_path):
    _init_vite_react_repo(tmp_path)
    ws = workspace(tmp_path)
    snapshot = build_repository_snapshot(ws)
    evidence = check_existence(ws, snapshot, "authentication")
    assert evidence.status == ExistenceStatus.PARTIAL

    model = ScriptedModel(
        [
            {
                "summary": "Extend the existing partial authentication",
                "steps": [{"id": "step-1", "description": "Extend session.ts", "files": ["src/auth/session.ts"], "commands": [], "risks": [], "success_criteria": ["auth extended"]}],
                "files": ["src/auth/session.ts"],
                "file_actions": [{"path": "src/auth/session.ts", "action": "modify", "justification": "authentication is already partially implemented in session.ts"}],
                "tests": [],
                "commands": [],
                "risks": [],
                "success_criteria": ["auth extended"],
            },
            {"type": "tool_call", "tool": "read_file", "arguments": {"path": "src/auth/session.ts"}},
            {
                "type": "tool_call",
                "tool": "write_file",
                "arguments": {
                    "path": "src/auth/session.ts",
                    "content": "export function createSession() {\n  return { token: 'real-token' };\n}\n",
                },
            },
            {"type": "final", "message": "Extended the existing session handling."},
        ]
    )
    loop, executor = _build_loop(model, tmp_path)
    task = TaskState(workspace_path=tmp_path, goal="Implement authentication", max_iterations=10)

    completed = loop.run(ws, task)

    assert completed.status == TaskStatus.COMPLETED
    plan = TaskPlan.model_validate(completed.plan)
    assert plan.file_actions[0].action == FileActionType.MODIFY

    # No new "auth"-ish file was created — the existing session.ts was reused.
    _, memory = loop.store.load(task.task_id)
    summary = _verified_summary(memory)
    assert summary["created_files"] == []
    assert "src/auth/session.ts" in summary["modified_files"]
