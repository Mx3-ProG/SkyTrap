"""Item 3 — CONSUME REPOSITORY MEMORY.

RepositoryMemory used to be write-only (persisted after a completed task, never
read back). This proves it's now actually consumed: a fingerprint-matching prior
run skips re-parsing the repository (memory_hit, real parsed symbols restored
without Tree-sitter running again), a changed repository correctly invalidates
the cache rather than trusting stale data, and the current repository state is
always what a hit/miss decision is checked against.
"""

import json
import sqlite3
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
)
from skytrap.autonomy.loop import AgentLoop
from skytrap.core.context import detect_workspace
from skytrap.intelligence.repository_memory import RepositoryMemory, RepositoryMemoryStore
from skytrap.intelligence.snapshot import build_repository_snapshot
from skytrap.intelligence.symbols import SymbolIndex
from skytrap.models.base import ModelProvider
from skytrap.tools.filesystem import ReadFileTool, WriteFileTool


def workspace(path: Path):
    # A real git workspace — the fingerprint (HEAD + dirty flag) that memory
    # hit/miss/invalidation depends on only changes across real commits.
    return detect_workspace(path)


class ScriptedModel(ModelProvider):
    name = "scripted"
    engine = "LOCAL"

    def __init__(self, responses):
        self.responses = responses
        self.calls = 0

    def chat(self, messages):
        response = self.responses[self.calls]
        self.calls += 1
        return response if isinstance(response, str) else json.dumps(response)


class TrivialPassVerifier(VerificationLoop):
    def discover(self, workspace):
        return {
            VerificationStage.LINT: [],
            VerificationStage.TYPECHECK: [],
            VerificationStage.TEST: ["python3 -c 'pass'"],
            VerificationStage.BUILD: [],
        }


def _init_repo(root: Path) -> None:
    (root / "app.py").write_text("def handler():\n    return 'ok'\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@e.com", "commit", "-q", "-m", "init"],
        cwd=root,
        check=True,
    )


def _run_trivial_task(tmp_path: Path, store: RepositoryMemoryStore, goal: str = "Investigate the handler function") -> AgentLoop:
    ws = workspace(tmp_path)
    model = ScriptedModel(
        [
            {
                "summary": "Inspect handler",
                "steps": [],
                "files": ["app.py"],
                "file_actions": [{"path": "app.py", "action": "keep", "justification": "read-only task"}],
                "tests": [],
                "commands": [],
                "risks": [],
                "success_criteria": ["explained"],
            },
            {"type": "tool_call", "tool": "read_file", "arguments": {"path": "app.py"}},
            {"type": "final", "message": "It returns 'ok'."},
        ]
    )
    executor = ToolExecutor(
        [ReadFileTool(), WriteFileTool(confirm=lambda _: True)],
        RiskEngine(),
        ApprovalEngine(),
        capabilities={Capability.FILESYSTEM_READ, Capability.FILESYSTEM_WRITE},
    )
    task_store = TaskStore(tmp_path / f".state-{model.calls}-{id(model)}")
    loop = AgentLoop(
        model,
        Planner(model),
        executor,
        TrivialPassVerifier(),
        task_store,
        repository_memory_store=store,
    )
    task = TaskState(workspace_path=tmp_path, goal=goal, max_iterations=10)
    completed = loop.run(ws, task)
    assert completed.status == TaskStatus.COMPLETED
    return loop


def test_symbol_index_restore_from_reproduces_a_fresh_build(tmp_path):
    _init_repo(tmp_path)
    ws = workspace(tmp_path)
    snapshot = build_repository_snapshot(ws)
    fresh = SymbolIndex().build(ws)

    memory = RepositoryMemory.from_snapshot(snapshot, symbol_index=fresh)
    restored = SymbolIndex.restore_from(memory.parsed_files)

    assert restored.find("handler") == fresh.find("handler")
    assert restored.files() == fresh.files()


def test_first_run_is_a_memory_miss_second_run_on_unchanged_repo_is_a_hit(tmp_path):
    _init_repo(tmp_path)
    connection = sqlite3.connect(":memory:")
    store = RepositoryMemoryStore(connection)

    first_loop = _run_trivial_task(tmp_path, store)
    assert first_loop.last_repository_memory_metrics["memory_miss"] is True
    assert first_loop.last_repository_memory_metrics["memory_hit"] is False

    # Simulate what AutonomousTaskService does after a completed task: persist
    # what was actually built, including the parsed symbols.
    snapshot = build_repository_snapshot(workspace(tmp_path))
    store.save(RepositoryMemory.from_snapshot(snapshot, symbol_index=first_loop.last_symbol_index))

    second_loop = _run_trivial_task(tmp_path, store)
    assert second_loop.last_repository_memory_metrics["memory_hit"] is True
    assert second_loop.last_repository_memory_metrics["memory_miss"] is False
    assert second_loop.last_repository_memory_metrics["discovery_time_saved_ms"] >= 0
    # The restored index must still be real, correct data — not a stub.
    assert second_loop.last_symbol_index.find("handler")


def test_changed_repository_invalidates_the_cache_instead_of_trusting_it(tmp_path):
    _init_repo(tmp_path)
    connection = sqlite3.connect(":memory:")
    store = RepositoryMemoryStore(connection)

    first_loop = _run_trivial_task(tmp_path, store)
    snapshot = build_repository_snapshot(workspace(tmp_path))
    store.save(RepositoryMemory.from_snapshot(snapshot, symbol_index=first_loop.last_symbol_index))

    # The repository changes — a new commit, a new symbol — so the fingerprint
    # the cache was saved under is now stale.
    (tmp_path / "app.py").write_text("def handler():\n    return 'ok'\n\ndef other():\n    return 1\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@e.com", "commit", "-q", "-m", "change"],
        cwd=tmp_path,
        check=True,
    )

    second_loop = _run_trivial_task(tmp_path, store)
    assert second_loop.last_repository_memory_metrics["memory_hit"] is False
    assert second_loop.last_repository_memory_metrics["memory_invalidated"] is True
    # The freshly-rebuilt index reflects the CURRENT repo state, not the cached one.
    assert second_loop.last_symbol_index.find("other")


def test_repository_memory_store_load_if_current_never_trusts_a_stale_fingerprint(tmp_path):
    connection = sqlite3.connect(":memory:")
    store = RepositoryMemoryStore(connection)
    store.save(RepositoryMemory(workspace_path="/repo", fingerprint="old-commit", architecture="Python"))

    # The current repository is the source of truth — a memory saved under a
    # different fingerprint must never be handed back as if it were current.
    assert store.load_if_current("/repo", "new-commit") is None
    assert store.load_if_current("/repo", "old-commit") is not None
