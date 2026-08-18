import shlex
import time

import typer
from rich.text import Text

import skytrap.tools.skills  # noqa: F401 - importing this runs every skill's @register_tool

from skytrap.core import processes
from skytrap.core.agent import run_agent_turn
from skytrap.core.context import detect_workspace
from skytrap.core.notes import run_summarizer
from skytrap.core.roles import run_architect, run_developer, run_reviewer
from skytrap.memory.sqlite import SqliteMemory
from skytrap.models.ollama import OllamaProvider
from skytrap.tools.base import Tool
from skytrap.tools.filesystem import ListDirectoryTool, ReadFileTool, WriteFileTool
from skytrap.tools.git import GitDiffTool, GitStatusTool, review_diff
from skytrap.tools.notes import GetPastNotesTool
from skytrap.tools.process import (
    ListBackgroundProcessesTool,
    StartBackgroundProcessTool,
    StopBackgroundProcessTool,
)
from skytrap.tools.registry import RegistryContext, build_registered_tools
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


def _build_full_toolset(
    on_write=None, state: ChatState | None = None, memory: SqliteMemory | None = None
) -> list[Tool]:
    """The complete, mutating toolset: everything a chat session or the Developer
    role can call. write_file and shell each keep their own confirmation gate.
    `on_write`, if given, is forwarded to WriteFileTool to track touched paths.
    `state`, if given, makes the confirmation gates mode-aware (auto-approved with
    the preview still shown when state.mode == "auto") — the `plan`/`build` CLI
    commands don't pass one, so they keep their normal always-confirm behavior.
    `memory`, if given, adds get_past_notes (read-only) so the agent can reorient
    using SkyTrap's own past work-log notes for this workspace."""
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

    tools: list[Tool] = [
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
    if memory is not None:
        tools.append(GetPastNotesTool(memory=memory))
        # Additive: any skill registered via @register_tool (tools/skills/) joins the
        # toolset here. Nothing above this line changes — existing tools are still a
        # plain hard-coded list, this just appends whatever skills exist on top.
        tools.extend(
            build_registered_tools(RegistryContext(memory=memory, confirm_write=write_confirm))
        )
    return tools


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return

    workspace = detect_workspace()
    model = OllamaProvider()
    state = ChatState()
    memory = SqliteMemory()
    touched_files: list[str] = []
    tools = _build_full_toolset(state=state, memory=memory, on_write=touched_files.append)
    history: list[dict] = []
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
        # Only worth a note if something actually happened — not for a two-message
        # "hi"/"hello" exchange. len(history) counts message-pairs from completed
        # normal/auto-mode turns (plan-mode turns are intentionally stateless).
        if len(history) >= 4 or touched_files:
            transcript = "\n".join(f"{m['role']}: {m['content']}" for m in history)
            try:
                note = run_summarizer(model, workspace, "Interactive chat session", transcript)
                memory.record_note(session_id, str(workspace.path), note)
            except Exception:  # noqa: BLE001 - never let note-writing crash the exit path
                pass
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
    memory = SqliteMemory()
    session_id = memory.start_session(str(workspace.path))

    try:
        console.print("[dim]Architect is analyzing the workspace...[/dim]")
        plan_text = run_architect(model, workspace, task, memory=memory)
        print_plan(plan_text, note="")

        if not confirm_implement_plan():
            console.print("[dim]Cancelled — nothing was changed.[/dim]")
            return

        touched_files: list[str] = []
        tools = _build_full_toolset(on_write=touched_files.append, memory=memory)
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
            model,
            workspace,
            task,
            diff_result.output,
            test_result.output,
            test_result.success,
            memory=memory,
        )
        print_review(review)

        # End-of-task boundary: this is where a note is genuinely worth writing —
        # a completed build, not a cancelled or no-op one.
        outcome = f"Developer summary:\n{summary}\n\nReviewer findings:\n{review}"
        try:
            note = run_summarizer(model, workspace, task, outcome)
            memory.record_note(session_id, str(workspace.path), note)
        except Exception:  # noqa: BLE001 - never let note-writing fail the build
            pass
    finally:
        memory.close()


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


@app.command()
def create_user(email: str) -> None:
    """Create the single user account for the web control backend (login/OTP over
    HTTP — see `skytrap serve-web`). There is no HTTP signup route on purpose: this
    is a single-user system, AuthStore.create_user already refuses a second
    account, so an HTTP signup endpoint would only add attack surface for nothing."""
    from skytrap.server.auth.store import AuthStore

    password = typer.prompt("Password", hide_input=True, confirmation_prompt=True)

    store = AuthStore()
    try:
        user = store.create_user(email, password)
    except ValueError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(1) from exc
    finally:
        store.close()

    console.print(f"[green]Created user[/green] #{user.id}: {user.email}")


@app.command()
def serve_web(
    host: str = "127.0.0.1",
    port: int = 8000,
    tailscale: bool = typer.Option(
        False,
        "--tailscale",
        help=(
            "Also expose this server on your tailnet over HTTPS via `tailscale "
            "serve`, so it's reachable from your phone anywhere — not just this "
            "machine's local network. Requires Tailscale installed, logged in, "
            "with MagicDNS and HTTPS Certificates enabled in the admin console."
        ),
    ),
) -> None:
    """Start the SkyTrap web control backend (FastAPI): auth, turns, WebSocket
    streaming, and the PWA itself if built (see `frontend/`). Distinct from
    `skytrap serve`, which starts an arbitrary background process (e.g. a dev
    server) in the current workspace — same verb, different job."""
    import uvicorn

    if tailscale:
        from skytrap.core.tailscale import TailscaleError, enable_serve

        try:
            url = enable_serve(port)
        except TailscaleError as exc:
            console.print(f"[bold red]Tailscale error:[/bold red] {exc}")
            raise typer.Exit(1) from exc
        console.print(f"[green]Reachable on your tailnet at:[/green] {url}")

    uvicorn.run("skytrap.server.app:app", host=host, port=port)
