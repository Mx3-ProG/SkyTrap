import io
import subprocess
from types import SimpleNamespace

import httpx
import typer
from rich.console import Console

import skytrap.cli as cli
from skytrap.core.context import WorkspaceContext, detect_workspace, git_worktree_state
from skytrap.memory.sqlite import SqliteMemory
from skytrap.models.ollama import OllamaProvider
from skytrap.ui.terminal import (
    ChatState,
    TerminalCapabilities,
    build_rabbit_prompt,
    generate_command_map,
    print_agent_event,
    print_command_map,
    print_risk_action,
    print_startup_dashboard,
)


def capture_console(width=100):
    stream = io.StringIO()
    return stream, Console(file=stream, width=width, color_system=None, force_terminal=False)


def test_command_map_is_generated_from_live_typer_tree():
    entries = generate_command_map(typer.main.get_command(cli.app))
    commands = {entry.command for entry in entries}

    assert "skytrap" in commands
    assert 'skytrap agent run PATH "GOAL"' in commands
    assert "skytrap agent status" in commands
    assert "skytrap agent resume TASK_ID" in commands
    assert "skytrap agent stop TASK_ID" in commands
    assert "skytrap agent rollback TASK_ID" in commands
    assert 'skytrap plan "TASK"' in commands
    assert "skytrap security" in commands
    assert "skytrap commands" in commands


def test_ascii_no_color_fallback_is_clean_and_fits_small_terminal(tmp_path):
    stream, target = capture_console(width=42)
    caps = TerminalCapabilities(unicode=False, color=False)
    workspace = WorkspaceContext(path=tmp_path, name=tmp_path.name, is_git=False)

    print_startup_dashboard(
        workspace,
        "mock-model",
        False,
        "not a git repository",
        "LOCAL / normal",
        "ready",
        target_console=target,
        capabilities=caps,
    )
    print_command_map(
        generate_command_map(typer.main.get_command(cli.app))[:3],
        target_console=target,
        capabilities=caps,
    )
    output = stream.getvalue()

    assert "( o.o)  SKYTRAP" in output
    assert "RABBIT HOLE" in output
    assert "ollama" in output and "offline" in output
    assert "TREASURE MAP" in output
    assert "\x1b[" not in output
    assert "◇" not in output and "╭" not in output and "✓" not in output
    assert max(map(len, output.splitlines())) <= 42


def test_dashboard_with_git_repo_and_without_git_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    plain_workspace = detect_workspace(plain)
    assert git_worktree_state(plain_workspace) == "not a git repository"

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("one\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", "init"],
        cwd=repo,
        check=True,
    )
    git_workspace = detect_workspace(repo)
    assert git_worktree_state(git_workspace) == "clean"
    (repo / "tracked.txt").write_text("two\n")
    assert git_worktree_state(git_workspace) == "dirty"

    stream, target = capture_console()
    print_startup_dashboard(
        git_workspace,
        "model",
        True,
        "dirty",
        "LOCAL / normal",
        "ready",
        target_console=target,
        capabilities=TerminalCapabilities(unicode=True, color=False),
    )
    output = stream.getvalue()
    assert "branch" in output and git_workspace.branch in output
    assert "dirty" in output


def test_ollama_inaccessible_status_never_raises(monkeypatch):
    def unavailable(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "get", unavailable)
    assert OllamaProvider().is_available(timeout=0.01) is False


def test_agent_events_only_render_real_event_payloads():
    stream, target = capture_console()
    caps = TerminalCapabilities(unicode=True, color=False)
    events = [
        {"kind": "exploration_started", "target": "src/auth"},
        {"kind": "plan_created", "steps": 3, "files": 14},
        {
            "kind": "tool_result",
            "tool": "patch_file",
            "arguments": {"path": "src/auth.py"},
            "success": True,
        },
        {"kind": "verification_stage", "stage": "test", "success": True, "skipped": False},
        {"kind": "retry", "revision": 2},
        {"kind": "checkpoint", "success": True},
        {"kind": "task_completed", "task": {}},
    ]
    for event in events:
        print_agent_event(event, target_console=target, capabilities=caps)
    print_agent_event(
        {"kind": "task_state", "task": {"status": "running"}},
        target_console=target,
        capabilities=caps,
    )
    output = stream.getvalue()

    assert "◇ descending into src/auth" in output
    assert "◆ mapped 3 steps · 14 files" in output
    assert "⚒ patch_file src/auth.py · done" in output
    assert "test ........ PASS" in output
    assert "↻ following another tunnel · revision 2" in output
    assert "✦ checkpoint sealed" in output
    assert "TREASURE FOUND" in output
    assert "task_state" not in output


def test_high_and_critical_risk_panels_are_unmistakable():
    stream, target = capture_console()
    caps = TerminalCapabilities(unicode=True, color=False)
    print_risk_action(
        "HIGH",
        "git push origin skytrap/task-123",
        "git:push",
        target_console=target,
        capabilities=caps,
    )
    print_risk_action(
        "CRITICAL",
        "rm protected.file",
        "shell:execute",
        target_console=target,
        capabilities=caps,
    )
    output = stream.getvalue()
    assert "⚠ HIGH RISK ACTION" in output
    assert "⚠ CRITICAL RISK ACTION" in output
    assert "git push origin skytrap/task-123" in output
    assert "Scope: git:push" in output


def test_rabbit_prompt_has_workspace_and_ascii_fallback(tmp_path):
    workspace = WorkspaceContext(path=tmp_path, name=tmp_path.name, is_git=False)
    prompt = build_rabbit_prompt(
        workspace, ChatState(), TerminalCapabilities(unicode=False, color=False)
    )
    assert prompt == f"rabbit@skytrap {tmp_path} [normal] $ "


def test_interactive_startup_calls_dashboard_and_command_map(tmp_path, monkeypatch):
    calls = []

    class Model:
        name = "mock-ollama"
        engine = "LOCAL"
        cost_eur = 0.0

        def is_available(self):
            return False

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "OllamaProvider", Model)
    monkeypatch.setattr(cli, "SqliteMemory", lambda: SqliteMemory(tmp_path / "memory.db"))
    monkeypatch.setattr(cli, "print_startup_dashboard", lambda *args, **kwargs: calls.append("dashboard"))
    monkeypatch.setattr(cli, "print_command_map", lambda *args, **kwargs: calls.append("map"))
    monkeypatch.setattr(cli, "run_chat_loop", lambda *args, **kwargs: calls.append("loop"))

    cli.main(SimpleNamespace(invoked_subcommand=None))

    assert calls == ["dashboard", "map", "loop"]
