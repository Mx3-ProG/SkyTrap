import json
from pathlib import Path

from skytrap.autonomy import (
    AgentLoop,
    ApprovalDecision,
    ApprovalEngine,
    ApprovalRequest,
    Capability,
    PatchEngine,
    Planner,
    RiskEngine,
    RiskLevel,
    TaskState,
    TaskStatus,
    TaskStore,
    ToolExecutor,
    VerificationLoop,
    VerificationStage,
    WorkingMemory,
)
from skytrap.core.context import WorkspaceContext
from skytrap.models.base import ModelProvider
from skytrap.tools.base import ToolResult
from skytrap.tools.filesystem import ReadFileTool, WriteFileTool


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


def valid_plan() -> dict:
    return {
        "summary": "Implement and verify",
        "steps": [
            {
                "id": "step-1",
                "description": "Write the answer",
                "files": ["answer.txt"],
                "commands": ["python3 check.py"],
                "risks": [],
                "success_criteria": ["check passes"],
            }
        ],
        "files": ["answer.txt"],
        "tests": ["python3 check.py"],
        "commands": ["python3 check.py"],
        "risks": [],
        "success_criteria": ["check passes"],
    }


def test_tool_result_has_normalized_status():
    assert ToolResult(success=True, output="ok").status == "succeeded"
    assert ToolResult(success=False, output="bad").status == "failed"


def test_task_store_round_trip_and_terminal_transition_guard(tmp_path):
    task = TaskState(workspace_path=tmp_path, goal="fix it")
    memory = WorkingMemory(objective=task.goal)
    memory.record("tool_result", tool="read_file", path="a.py", success=True)
    store = TaskStore(tmp_path / "states")

    path = store.save(task, memory)
    loaded_task, loaded_memory = store.load(task.task_id)

    assert path.exists()
    assert loaded_task == task
    assert loaded_memory.files_consulted == ["a.py"]
    task.transition(TaskStatus.COMPLETED)
    try:
        task.transition(TaskStatus.RUNNING)
    except ValueError as exc:
        assert "terminal task" in str(exc)
    else:
        raise AssertionError("terminal state transition should fail")


def test_patch_engine_detects_conflicts_and_rolls_back(tmp_path):
    target = tmp_path / "module.py"
    target.write_text("answer = 1\n")
    engine = PatchEngine()

    result = engine.apply_replacement(workspace(tmp_path), "module.py", "answer = 1", "answer = 2")

    assert result.success
    assert target.read_text() == "answer = 2\n"
    conflict = engine.apply_replacement(workspace(tmp_path), "module.py", "missing", "x")
    assert not conflict.success
    assert "found 0" in conflict.output
    rollback = engine.rollback(result.metadata["backup_id"])
    assert rollback.success
    assert target.read_text() == "answer = 1\n"


def test_executor_enforces_capabilities_and_pending_approval(tmp_path):
    task = TaskState(workspace_path=tmp_path, goal="read")
    memory = WorkingMemory(objective=task.goal)
    denied = ToolExecutor(
        [ReadFileTool()],
        RiskEngine(),
        ApprovalEngine(),
        capabilities={Capability.FILESYSTEM_WRITE},
    ).execute(task, memory, workspace(tmp_path), "read_file", {"path": "x"})
    assert denied.status == "denied"

    (tmp_path / "delete-me.txt").write_text("x")
    pending = ToolExecutor(
        [], RiskEngine(), ApprovalEngine(), capabilities={Capability.FILESYSTEM_WRITE}
    )
    assessment = pending.risk_engine.assess("delete_file", {"path": "delete-me.txt"})
    assert assessment.level == RiskLevel.HIGH
    assert pending.approval_engine.decide(
        ApprovalRequest(
            task_id=task.task_id,
            tool_name="delete_file",
            arguments={"path": "delete-me.txt"},
            assessment=assessment,
        )
    ) == ApprovalDecision.PENDING


def test_verification_loop_is_fail_fast_and_structured(tmp_path):
    loop = VerificationLoop()
    result = loop.run(
        workspace(tmp_path),
        {
            VerificationStage.LINT: ["python3 -c pass"],
            VerificationStage.TYPECHECK: [],
            VerificationStage.TEST: ["python3 -c 'import sys; sys.exit(3)'"],
            VerificationStage.BUILD: ["python3 -c pass"],
        },
    )
    assert not result.success
    assert result.failed_stage == VerificationStage.TEST
    assert result.results[-1].exit_code == 3
    assert len(result.results) == 2


class CheckFileVerifier(VerificationLoop):
    def discover(self, workspace):
        return {
            VerificationStage.LINT: [],
            VerificationStage.TYPECHECK: [],
            VerificationStage.TEST: ["python3 check.py"],
            VerificationStage.BUILD: [],
        }


def test_agent_loop_fixes_failed_verification_and_persists_completion(tmp_path):
    (tmp_path / "check.py").write_text(
        "from pathlib import Path\n"
        "raise SystemExit(0 if Path('answer.txt').read_text() == 'good' else 1)\n"
    )
    model = ScriptedModel(
        [
            valid_plan(),
            {"type": "tool_call", "tool": "write_file", "arguments": {"path": "answer.txt", "content": "bad"}},
            {"type": "final", "message": "done"},
            {"type": "tool_call", "tool": "write_file", "arguments": {"path": "answer.txt", "content": "good"}},
            {"type": "final", "message": "fixed"},
        ]
    )
    planner = Planner(model)
    executor = ToolExecutor(
        [WriteFileTool(confirm=lambda _: True)],
        RiskEngine(),
        ApprovalEngine(),
        capabilities={Capability.FILESYSTEM_WRITE},
    )
    store = TaskStore(tmp_path / ".state")
    loop = AgentLoop(model, planner, executor, CheckFileVerifier(), store)
    task = TaskState(workspace_path=tmp_path, goal="make the check pass", max_iterations=8)

    completed = loop.run(workspace(tmp_path), task)
    persisted, memory = store.load(task.task_id)

    assert completed.status == TaskStatus.COMPLETED
    assert persisted.status == TaskStatus.COMPLETED
    assert (tmp_path / "answer.txt").read_text() == "good"
    assert len(memory.verification_results) == 2
    assert memory.verification_results[0]["success"] is False
    assert memory.verification_results[1]["success"] is True
    assert completed.plan["revision"] == 2


def test_agent_loop_stops_cleanly_and_can_resume(tmp_path):
    (tmp_path / "check.py").write_text(
        "from pathlib import Path\n"
        "raise SystemExit(0 if Path('answer.txt').read_text() == 'good' else 1)\n"
    )
    model = ScriptedModel([valid_plan()])
    store = TaskStore(tmp_path / ".state")
    loop = AgentLoop(
        model,
        Planner(model),
        ToolExecutor([], RiskEngine(), ApprovalEngine()),
        CheckFileVerifier(),
        store,
        should_stop=lambda: True,
    )
    task = TaskState(workspace_path=tmp_path, goal="stop", max_iterations=2)

    stopped = loop.run(workspace(tmp_path), task)

    assert stopped.status == TaskStatus.CANCELLED
    loaded, _ = store.load(task.task_id)
    assert loaded.status == TaskStatus.CANCELLED

    resumed_model = ScriptedModel(
        [
            {"type": "tool_call", "tool": "write_file", "arguments": {"path": "answer.txt", "content": "good"}},
            {"type": "final", "message": "resumed and fixed"},
        ]
    )
    resumed_loop = AgentLoop(
        resumed_model,
        Planner(resumed_model),
        ToolExecutor(
            [WriteFileTool(confirm=lambda _: True)],
            RiskEngine(),
            ApprovalEngine(),
            capabilities={Capability.FILESYSTEM_WRITE},
        ),
        CheckFileVerifier(),
        store,
    )
    resumed = resumed_loop.resume(workspace(tmp_path), task.task_id)

    assert resumed.status == TaskStatus.COMPLETED
    assert resumed.run_id != stopped.run_id
    assert (tmp_path / "answer.txt").read_text() == "good"
