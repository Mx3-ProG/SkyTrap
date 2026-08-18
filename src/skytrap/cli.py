import shlex
import time

import typer
from rich.text import Text

from skytrap.core import processes
from skytrap.core.agent import run_agent_turn
from skytrap.core.context import detect_workspace
from skytrap.core.roles import run_architect, run_developer, run_reviewer
from skytrap.memory.sqlite import SqliteMemory
from skytrap.models.ollama import OllamaProvider
from skytrap.tools.base import Tool
from skytrap.tools.filesystem import ListDirectoryTool, ReadFileTool, WriteFileTool
from skytrap.tools.git import GitDiffTool, GitStatusTool, review_diff
from skytrap.tools.process import (
    ListBackgroundProcessesTool,
    StartBackgroundProcessTool,
    StopBackgroundProcessTool,
)
from skytrap.tools.search import SearchCodeTool
from skytrap.tools.shell import ShellTool
from skytrap.tools.tests import RunTestsTool
from skytrap.tools.verification import (
    AccessibilityCheckTool,
    CssLintTool,
    HtmlLintTool,
    LighthouseAuditTool,
)
from skytrap.ui.terminal import (
    ChatState,
    confirm_implement_plan,
    confirm_shell,
    confirm_start_process,
    confirm_stop_process,
    confirm_write,
    console,
    make_mode_aware_confirm,
    print_banner,
    print_developer_summary,
    print_diff_summary,
    print_plan,
    print_review,
    print_test_result,
    run_chat_loop,
)

app = typer.Typer(add_completion=False, invoke_without_command=True)


def _build_full_toolset(on_write=None, state: ChatState | None = None) -> list[Tool]:
    """The complete, mutating toolset: everything a chat session or the Developer
    role can call. write_file and shell each keep their own confirmation gate.
    `on_write`, if given, is forwarded to WriteFileTool to track touched paths.
    `state`, if given, makes the confirmation gates mode-aware (auto-approved with
    the preview still shown when state.mode == "auto") — the `plan`/`build` CLI
    commands don't pass one, so they keep their normal always-confirm behavior."""
    if state is not None:
        write_confirm = make_mode_aware_confirm(confirm_write, state)
        shell_confirm = make_mode_aware_confirm(confirm_shell, state)
        start_process_confirm = make_mode_aware_confirm(confirm_start_process, state)
        stop_process_confirm = make_mode_aware_confirm(confirm_stop_process, state)
    else:
        write_confirm = confirm_write
        shell_confirm = confirm_shell
        start_process_confirm = confirm_start_process
        stop_process_confirm = confirm_stop_process

    return [
        ReadFileTool(),
        ListDirectoryTool(),
        SearchCodeTool(),
        GitStatusTool(),
        GitDiffTool(),
        WriteFileTool(confirm=write_confirm, on_write=on_write),
        ShellTool(confirm=shell_confirm),
        RunTestsTool(),
        LighthouseAuditTool(),
        AccessibilityCheckTool(),
        HtmlLintTool(),
        CssLintTool(),
        StartBackgroundProcessTool(confirm=start_process_confirm),
        ListBackgroundProcessesTool(),
        StopBackgroundProcessTool(confirm=stop_process_confirm),
    ]


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return

    workspace = detect_workspace()
    model = OllamaProvider()
    state = ChatState()
    tools = _build_full_toolset(state=state)
    history: list[dict] = []
    memory = SqliteMemory()
    session_id = memory.start_session(str(workspace.path))

    def respond(user_input: str, chat_state: ChatState) -> None:
        if chat_state.mode == "plan":
            console.print("[dim]Architect is analyzing (read-only)...[/dim]")
            result = run_architect(model, workspace, user_input)
            memory.record_message(session_id, "user", user_input)
            memory.record_message(session_id, "assistant", result)
            print_plan(
                result,
                note="Read-only — nothing changed. Cycle mode (Shift+Tab) to implement.",
            )
            return

        console.print("[dim]thinking...[/dim]")
        reply = run_agent_turn(model, tools, workspace, history, user_input)
        memory.record_message(session_id, "user", user_input)
        memory.record_message(session_id, "assistant", reply)
        console.print(Text(reply))

    print_banner(model, workspace)
    try:
        run_chat_loop(respond, state=state)
    finally:
        memory.close()


@app.command()
def plan(task: str) -> None:
    """Analyze TASK against this workspace and print an implementation plan.
    Read-only: the Architect role has no write_file/shell/run_tests access, so
    nothing in the workspace is changed."""
    workspace = detect_workspace()
    model = OllamaProvider()
    console.print("[dim]Architect is analyzing the workspace...[/dim]")
    result = run_architect(model, workspace, task)
    print_plan(result)


@app.command()
def build(task: str) -> None:
    """Plan TASK with the Architect, then — only if you approve the plan — implement
    it with the Developer role, run the test suite, and show a diff summary. Every
    write_file/shell call still asks for its own individual confirmation on top of
    approving the plan up front."""
    workspace = detect_workspace()
    model = OllamaProvider()

    console.print("[dim]Architect is analyzing the workspace...[/dim]")
    plan_text = run_architect(model, workspace, task)
    print_plan(plan_text, note="")

    if not confirm_implement_plan():
        console.print("[dim]Cancelled — nothing was changed.[/dim]")
        return

    touched_files: list[str] = []
    tools = _build_full_toolset(on_write=touched_files.append)
    console.print("[dim]Developer is implementing the plan...[/dim]")
    summary = run_developer(model, tools, workspace, task, plan_text)
    print_developer_summary(summary)

    console.print("[dim]Running the test suite...[/dim]")
    test_result = RunTestsTool().execute(workspace, {})
    print_test_result(test_result.output, test_result.success)

    if not touched_files:
        console.print("[dim]No files were written.[/dim]")
        return

    # Scoped to what the Developer actually wrote, not the whole working tree —
    # otherwise pre-existing unrelated uncommitted changes would show up here too.
    diff_result = review_diff(workspace, touched_files)
    if not diff_result.success:
        return
    print_diff_summary(diff_result.output)

    console.print("[dim]Reviewer is analyzing the diff...[/dim]")
    review = run_reviewer(
        model, workspace, task, diff_result.output, test_result.output, test_result.success
    )
    print_review(review)


@app.command()
def serve(command: str) -> None:
    """Start COMMAND (e.g. "npm run dev") as a persistent background process that
    keeps running after this exits. No confirmation needed — you typed the exact
    command yourself. Use `skytrap ps` to check on it, `skytrap stop <id>` to stop it."""
    workspace = detect_workspace()
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        console.print(f"[bold red]Error:[/bold red] could not parse command: {exc}")
        raise typer.Exit(1) from exc
    if not tokens:
        console.print("[bold red]Error:[/bold red] empty command")
        raise typer.Exit(1)

    record = processes.start_process(str(workspace.path), tokens)
    time.sleep(1)  # give it a moment to fail fast (missing deps, command not found, ...)

    if not processes.is_running(record.pid):
        console.print(f"[bold red]Process exited immediately[/bold red] (pid {record.pid})")
        console.print(Text(processes.tail_log(record)))
        raise typer.Exit(1)

    console.print(f"[green]Started[/green] #{record.id} (pid {record.pid}): {command}")
    console.print(f"[dim]Log: {record.log_path}[/dim]")


@app.command()
def ps() -> None:
    """List tracked background processes across all workspaces."""
    records = processes.list_processes()
    if not records:
        console.print("[dim]No tracked background processes.[/dim]")
        return

    for record in records:
        line = Text()
        line.append(f"#{record.id} ")
        if record.running:
            line.append("[running]", style="green")
        else:
            line.append("[stopped]", style="dim")
        line.append(f" pid {record.pid}: {record.command} ({record.workspace_path})")
        console.print(line)


@app.command()
def stop(process_id: int) -> None:
    """Stop a tracked background process by id (see `skytrap ps`)."""
    ok, message = processes.stop_process(process_id)
    style = "green" if ok else "yellow"
    console.print(f"[{style}]{message}[/{style}]")
