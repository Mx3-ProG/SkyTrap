import json
import subprocess

from typer.testing import CliRunner

import skytrap.cli as cli
from skytrap.autonomy import TaskState, TaskStatus, TaskStore, WorkingMemory
from skytrap.models.base import ModelProvider


class ScriptedModel(ModelProvider):
    name = "scripted"
    engine = "LOCAL"

    def __init__(self, responses: list[dict]):
        self.responses = responses
        self.calls = 0

    def chat(self, messages: list[dict]) -> str:
        response = self.responses[self.calls]
        self.calls += 1
        return json.dumps(response)


def init_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    (path / "app.py").write_text("answer = 'broken'\n")
    (path / "test_app.py").write_text(
        "import unittest\n"
        "import app\n\n"
        "class AppTest(unittest.TestCase):\n"
        "    def test_answer(self):\n"
        "        self.assertEqual(app.answer, 'fixed')\n"
    )
    subprocess.run(["git", "add", "app.py", "test_app.py"], cwd=path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-q",
            "-m",
            "initial",
        ],
        cwd=path,
        check=True,
    )


def plan_response():
    return {
        "summary": "Fix the answer and verify it",
        "steps": [
            {
                "id": "step-1",
                "description": "Patch app.py and run tests",
                "files": ["app.py"],
                "commands": ["python3 -m unittest"],
                "risks": [],
                "success_criteria": ["Tests pass"],
            }
        ],
        "files": ["app.py"],
        "tests": ["python3 -m unittest"],
        "commands": ["python3 -m unittest"],
        "risks": [],
        "success_criteria": ["Tests pass"],
    }


def patch(expected, replacement):
    return {
        "type": "tool_call",
        "tool": "patch_file",
        "arguments": {
            "path": "app.py",
            "expected": expected,
            "replacement": replacement,
        },
    }


def test_agent_run_retries_checkpoints_reports_status_and_rolls_back(
    tmp_path, monkeypatch
):
    repo = tmp_path / "project"
    repo.mkdir()
    init_repo(repo)
    state_dir = tmp_path / "task-state"
    monkeypatch.setenv("SKYTRAP_STATE_DIR", str(state_dir))
    model = ScriptedModel(
        [
            plan_response(),
            patch("answer = 'broken'", "answer = 'still-wrong'"),
            {"type": "final", "message": "first attempt"},
            patch("answer = 'still-wrong'", "answer = 'fixed'"),
            {"type": "final", "message": "fixed after verification feedback"},
        ]
    )
    monkeypatch.setattr(cli, "OllamaProvider", lambda: model)
    runner = CliRunner()

    result = runner.invoke(
        cli.app,
        ["agent", "run", str(repo), "Fix the broken answer and verify tests"],
    )

    assert result.exit_code == 0, result.output
    tasks = TaskStore(state_dir).list_tasks()
    assert len(tasks) == 1
    task = tasks[0]
    assert task.status == TaskStatus.COMPLETED
    assert task.task_branch == f"skytrap/task-{task.task_id}"
    assert task.checkpoint_commit and task.checkpoint_commit != task.base_commit
    assert task.final_diff and "+answer = 'fixed'" in task.final_diff
    assert task.plan["revision"] == 2
    assert task.execution_evidence
    assert task.review_result and task.review_result["passed"] is True
    assert (repo / "app.py").read_text() == "answer = 'fixed'\n"
    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert branch == task.task_branch
    assert subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip() == ""

    status = runner.invoke(cli.app, ["agent", "status", task.task_id])
    assert status.exit_code == 0
    assert "completed" in status.output
    assert task.checkpoint_commit in status.output

    rollback = runner.invoke(cli.app, ["agent", "rollback", task.task_id], input="y\n")
    assert rollback.exit_code == 0, rollback.output
    assert (repo / "app.py").read_text() == "answer = 'broken'\n"
    rolled_back = TaskStore(state_dir).load(task.task_id)[0]
    assert rolled_back.rolled_back is True


def test_agent_stop_sets_durable_cooperative_stop_flag(tmp_path, monkeypatch):
    state_dir = tmp_path / "task-state"
    monkeypatch.setenv("SKYTRAP_STATE_DIR", str(state_dir))
    store = TaskStore(state_dir)
    task = TaskState(workspace_path=tmp_path, goal="long task", status=TaskStatus.RUNNING)
    store.save(task, WorkingMemory(objective=task.goal))
    monkeypatch.setattr(cli, "OllamaProvider", lambda: ScriptedModel([]))

    result = CliRunner().invoke(cli.app, ["agent", "stop", task.task_id])

    assert result.exit_code == 0
    persisted, _ = store.load(task.task_id)
    assert persisted.stop_requested is True


def test_agent_resume_continues_persisted_task_on_its_branch(tmp_path, monkeypatch):
    repo = tmp_path / "project"
    repo.mkdir()
    init_repo(repo)
    state_dir = tmp_path / "task-state"
    monkeypatch.setenv("SKYTRAP_STATE_DIR", str(state_dir))
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    task = TaskState(
        workspace_path=repo,
        goal="resume and fix",
        status=TaskStatus.CANCELLED,
        plan=plan_response(),
        original_branch="main",
        base_commit=base,
    )
    task.task_branch = f"skytrap/task-{task.task_id}"
    subprocess.run(["git", "switch", "-q", "-c", task.task_branch], cwd=repo, check=True)
    store = TaskStore(state_dir)
    store.save(task, WorkingMemory(objective=task.goal))
    old_run_id = task.run_id
    model = ScriptedModel(
        [
            patch("answer = 'broken'", "answer = 'fixed'"),
            {"type": "final", "message": "resumed"},
        ]
    )
    monkeypatch.setattr(cli, "OllamaProvider", lambda: model)

    result = CliRunner().invoke(cli.app, ["agent", "resume", task.task_id])

    assert result.exit_code == 0, result.output
    resumed, _ = store.load(task.task_id)
    assert resumed.status == TaskStatus.COMPLETED
    assert resumed.run_id != old_run_id
    assert resumed.checkpoint_commit != base
    assert (repo / "app.py").read_text() == "answer = 'fixed'\n"
