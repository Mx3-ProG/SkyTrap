import shlex
import time
from pathlib import Path

import typer
from rich.text import Text

import skytrap.tools.skills  # noqa: F401 - importing this runs every skill's @register_tool

from skytrap.autonomy.approval import ApprovalEngine, ApprovalRequest
from skytrap.autonomy.executor import ToolExecutor
from skytrap.autonomy.memory import WorkingMemory
from skytrap.autonomy.risk import FULL_INTERACTIVE_CAPABILITIES, RiskEngine, RiskLevel
from skytrap.autonomy.service import AutonomousTaskService
from skytrap.autonomy.state import TaskState, TaskStatus
from skytrap.core import processes
from skytrap.core.agent import run_agent_turn
from skytrap.core.context import WorkspaceContext, detect_workspace, git_worktree_state
from skytrap.core.doctor import DEGRADED, HEALTHY, build_fix_plan, intelligence_health_report, run_doctor
from skytrap.core.intent import detect_execution_intent
from skytrap.core.notes import run_summarizer
from skytrap.core.project_inspection import inspect_project
from skytrap.core.project_notes import append_journal_entry
from skytrap.core.roles import DEVELOPER_MAX_STEPS, run_architect, run_developer, run_reviewer
from skytrap.memory.sqlite import SqliteMemory
from skytrap.models.ollama import OllamaProvider
from skytrap.models.router import configured_ollama_router
from skytrap.security.cli import security_app
from skytrap.tools.base import Tool
from skytrap.tools.filesystem import DeleteFileTool, ListDirectoryTool, ReadFileTool, WriteFileTool
from skytrap.tools.git import GitDiffTool, GitStatusTool, git_file_action, review_diff
from skytrap.tools.notes import GetPastNotesTool
from skytrap.tools.process import (
    ListBackgroundProcessesTool,
    StartBackgroundProcessTool,
    StopBackgroundProcessTool,
)
from skytrap.tools.project import InspectProjectTool
from skytrap.tools.registry import RegistryContext, build_registered_tools
from skytrap.tools.search import SearchCodeTool
from skytrap.tools.security import SecurityAuditTool
from skytrap.tools.shell import ShellTool
from skytrap.tools.structural_search import StructuralSearchTool
from skytrap.tools.tests import RunTestsTool
from skytrap.tools.verification import (
    AccessibilityCheckTool,
    CssLintTool,
    HtmlLintTool,
    LighthouseAuditTool,
)
from skytrap.ui.terminal import (
    AgentRenderer,
    ChatState,
    confirm_implement_plan,
    console,
    generate_command_map,
    log_file,
    log_step,
    print_agent_event,
    print_command_map,
    print_developer_summary,
    print_diff_summary,
    print_plan,
    print_project_detected,
    print_review,
    print_risk_action,
    print_startup_dashboard,
    print_task_report,
    print_test_result,
    run_chat_loop,
)

app = typer.Typer(add_completion=False, invoke_without_command=True)
agent_app = typer.Typer(help="Run and manage persistent autonomous coding tasks.")
update_app = typer.Typer(help="Inspect trusted technology and model update sources.")
app.add_typer(security_app, name="security")
app.add_typer(agent_app, name="agent")
app.add_typer(update_app, name="update")

MAX_AUTOFIX_ATTEMPTS = 3


def _approval_action_label(request: ApprovalRequest) -> str:
    safe_arguments = {
        key: value
        for key, value in request.arguments.items()
        if key not in {"content", "replacement", "expected"}
    }
    return str(
        safe_arguments.get("command")
        or safe_arguments.get("path")
        or f"{request.tool_name} {safe_arguments}"
    )


def _approve_autonomous_action(request: ApprovalRequest, renderer: AgentRenderer) -> bool:
    renderer.render_risk_prompt(
        request.assessment.level.name,
        _approval_action_label(request),
        request.assessment.capability.value,
    )
    if request.assessment.reasons:
        console.print(Text(" · ".join(request.assessment.reasons), style="dim"))
    return typer.confirm("Approve this action?", default=False)


def _print_autonomous_result(task: TaskState) -> None:
    color = "green" if task.status == TaskStatus.COMPLETED else "yellow"
    console.print(f"[{color}]Task {task.task_id}: {task.status.value}[/{color}]")
    if task.task_branch:
        console.print(f"[dim]Branch: {task.task_branch}[/dim]")
    if task.checkpoint_commit:
        console.print(f"[dim]Checkpoint: {task.checkpoint_commit}[/dim]")
    if task.final_message:
        console.print(Text(task.final_message))
    if task.final_diff:
        print_diff_summary(task.final_diff)


def _autonomous_service(*, full_diff: bool = False) -> AutonomousTaskService:
    renderer = AgentRenderer(full_diff=full_diff)
    provider = OllamaProvider()

    def approval_callback(request: ApprovalRequest) -> bool:
        return _approve_autonomous_action(request, renderer)

    return AutonomousTaskService(
        provider,
        model_router=configured_ollama_router(provider),
        approval_callback=approval_callback,
        on_event=renderer.handle_event,
    )


@agent_app.command("run")
def agent_run(
    path: Path = typer.Argument(..., exists=True, file_okay=False, resolve_path=True),
    goal: str = typer.Argument(...),
    max_iterations: int = typer.Option(20, min=1, max=200, help="Maximum model iterations."),
    full_diff: bool = typer.Option(
        False, "--full-diff", help="Show every diff in full, without collapsing unchanged context lines."
    ),
) -> None:
    """Run GOAL autonomously in PATH on a dedicated Git branch."""
    service = _autonomous_service(full_diff=full_diff)
    task = service.run(path, goal, max_iterations=max_iterations)
    _print_autonomous_result(task)
    if task.status != TaskStatus.COMPLETED:
        raise typer.Exit(1)


@agent_app.command("resume")
def agent_resume(
    task_id: str,
    answer: str | None = typer.Option(
        None, "--answer", "-a", help="Answer a pending THE PATH FORKS clarification."
    ),
    full_diff: bool = typer.Option(
        False, "--full-diff", help="Show every diff in full, without collapsing unchanged context lines."
    ),
) -> None:
    """Resume a persisted task, optionally answering a pending clarification."""
    service = _autonomous_service(full_diff=full_diff)
    try:
        task = service.resume(task_id, clarification=answer)
    except (OSError, ValueError, RuntimeError) as exc:
        console.print(f"[bold red]Cannot resume task:[/bold red] {exc}")
        raise typer.Exit(1) from exc
    _print_autonomous_result(task)
    if task.status != TaskStatus.COMPLETED:
        raise typer.Exit(1)


@agent_app.command("status")
def agent_status(task_id: str | None = typer.Argument(None)) -> None:
    """Show one persisted task, or list recent tasks when TASK_ID is omitted."""
    service = _autonomous_service()
    if task_id is None:
        tasks = service.list_tasks()
        if not tasks:
            console.print("[dim]No autonomous tasks found.[/dim]")
            return
        for task in tasks:
            console.print(
                f"{task.task_id}  {task.status.value:<16}  {task.workspace_path}  {task.goal}"
            )
        return
    try:
        task = service.status(task_id)
    except (OSError, ValueError, KeyError) as exc:
        console.print(f"[bold red]Unknown task:[/bold red] {task_id}")
        raise typer.Exit(1) from exc
    _print_autonomous_result(task)


@agent_app.command("stop")
def agent_stop(task_id: str) -> None:
    """Request a cooperative stop; the running process persists a resumable state."""
    service = _autonomous_service()
    try:
        task = service.stop(task_id)
    except (OSError, ValueError, KeyError) as exc:
        console.print(f"[bold red]Cannot stop task:[/bold red] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"[yellow]Stop requested for task {task.task_id}.[/yellow]")


@agent_app.command("rollback")
def agent_rollback(task_id: str) -> None:
    """Reset the dedicated task branch to its recorded base commit."""
    service = _autonomous_service()
    if not typer.confirm(f"Rollback task {task_id} to its base commit?", default=False):
        raise typer.Abort()
    try:
        task = service.rollback(task_id)
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        console.print(f"[bold red]Rollback failed:[/bold red] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"[green]Rolled back {task.task_branch} to {task.base_commit}.[/green]")


def _always_approve(_preview: str) -> bool:
    """Item 1 — UNIFY EXECUTION: every mutating tool built by `_build_full_toolset`
    is wired with an always-approve `confirm`, matching
    `skytrap.autonomy.tools.build_autonomous_tools`'s convention exactly. A tool's
    own `confirm` callback is no longer a second, independent approval gate — the
    single gate is `ToolExecutor`'s RiskEngine + ApprovalEngine, which every write
    path (interactive chat, `skytrap build`, the web server, and `skytrap agent
    run`) now goes through identically. Wiring a real per-tool confirm here again
    would silently create a second policy and is exactly what this unification
    removes."""
    return True


def _build_full_toolset(
    workspace: WorkspaceContext,
    on_write=None,
    on_delete=None,
    memory: SqliteMemory | None = None,
) -> list[Tool]:
    """The complete, mutating toolset: everything a chat session or the Developer
    role can call. Every tool here is built with an always-approve `confirm` — the
    single approval boundary is the `ToolExecutor` (RiskEngine + ApprovalEngine)
    the caller wraps this list in (see `_build_full_executor` / `run_developer`),
    not a per-tool policy. `on_write`, if given, is forwarded to WriteFileTool to
    track touched paths. `memory`, if given, adds get_past_notes (read-only) so
    the agent can reorient using SkyTrap's own past work-log notes for this
    workspace."""
    on_write_logged = None
    if on_write is not None:

        def on_write_logged(path: str) -> None:  # noqa: F811 - intentional shadow
            log_file(path, git_file_action(workspace, path))
            on_write(path)

    on_delete_logged = None
    if on_delete is not None:

        def on_delete_logged(path: str) -> None:  # noqa: F811 - intentional shadow
            log_file(path, "D")
            on_delete(path)

    tools: list[Tool] = [
        ReadFileTool(),
        ListDirectoryTool(),
        SearchCodeTool(),
        StructuralSearchTool(),
        GitStatusTool(),
        GitDiffTool(),
        InspectProjectTool(),
        SecurityAuditTool(),
        WriteFileTool(confirm=_always_approve, on_write=on_write_logged),
        DeleteFileTool(confirm=_always_approve, on_delete=on_delete_logged),
        ShellTool(confirm=_always_approve, confirm_destructive=_always_approve),
        RunTestsTool(),
        LighthouseAuditTool(),
        AccessibilityCheckTool(),
        HtmlLintTool(),
        CssLintTool(),
        StartBackgroundProcessTool(confirm=_always_approve),
        ListBackgroundProcessesTool(),
        StopBackgroundProcessTool(confirm=_always_approve),
    ]
    if memory is not None:
        tools.append(GetPastNotesTool(memory=memory))
        # Additive: any skill registered via @register_tool (tools/skills/) joins the
        # toolset here. Nothing above this line changes — existing tools are still a
        # plain hard-coded list, this just appends whatever skills exist on top.
        tools.extend(
            build_registered_tools(RegistryContext(memory=memory, confirm_write=_always_approve))
        )
    return tools


def _build_full_executor(
    workspace: WorkspaceContext,
    *,
    memory: SqliteMemory | None = None,
    on_write=None,
    on_delete=None,
    approval_callback=None,
) -> ToolExecutor:
    """Item 1 — the one `ToolExecutor` interactive chat (`skytrap`) routes every
    write_file/patch_file/delete_file/shell call through — identically to
    `skytrap agent run` (see `AutonomousTaskService._loop`). Same RiskEngine, same
    ApprovalEngine, same inspect-before-write guard, same incremental symbol_index
    updates. `approval_callback` is what a HIGH/CRITICAL-risk action's approval
    routes through; `None` means such actions are left PENDING rather than
    silently approved."""
    tools = _build_full_toolset(workspace, on_write=on_write, on_delete=on_delete, memory=memory)
    return ToolExecutor(
        tools,
        RiskEngine(),
        ApprovalEngine(callback=approval_callback, auto_approve_through=RiskLevel.MEDIUM),
        capabilities=FULL_INTERACTIVE_CAPABILITIES,
    )


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return

    workspace = detect_workspace()
    model = OllamaProvider()
    state = ChatState()
    memory = SqliteMemory()
    touched_files: list[str] = []
    renderer = AgentRenderer()

    def mode_aware_approval(request: ApprovalRequest) -> bool:
        # Item 1 — the single RiskEngine/ApprovalEngine decides *whether* an
        # action is HIGH/CRITICAL risk identically in every mode; "auto" mode
        # only changes whether a human is actually asked once it gets here —
        # and CRITICAL (secrets/credentials) is never auto-approved, matching
        # the historical "destructive tier always asks" guarantee.
        if state.mode == "auto" and request.assessment.level < RiskLevel.CRITICAL:
            renderer.render_risk_prompt(
                request.assessment.level.name,
                _approval_action_label(request),
                request.assessment.capability.value,
            )
            console.print('[dim](auto-approved — mode is "auto")[/dim]')
            return True
        return _approve_autonomous_action(request, renderer)

    # Item 1 — UNIFY EXECUTION: the interactive session's one ToolExecutor, the
    # same RiskEngine/ApprovalEngine/inspect-before-write guard `skytrap agent
    # run` uses. `agent_task`/`agent_memory` persist across the whole session (not
    # rebuilt per message) so the write guard remembers a file read three turns
    # ago, and `executor.symbol_index` gets updated incrementally after each write.
    agent_task = TaskState(workspace_path=workspace.path, goal="interactive session")
    agent_memory = WorkingMemory(objective="interactive session")
    executor = _build_full_executor(
        workspace,
        memory=memory,
        on_write=touched_files.append,
        on_delete=touched_files.append,
        approval_callback=mode_aware_approval,
    )
    history: list[dict] = []
    session_id = memory.start_session(str(workspace.path))
    # Set whenever plan mode produces a plan, consumed if the very next message is
    # an execution trigger ("Go.", "Implémente le plan.") — otherwise plan-mode
    # turns carry no memory at all (they're excluded from `history`), so "Go." would
    # go right back to the read-only Architect with no idea what plan it just gave.
    last_plan: list[str] = []

    def on_step(step: dict) -> None:
        renderer.finish_activity()
        tool = step.get("tool")
        arguments = step.get("arguments") or {}
        metadata = step.get("metadata") or {}
        success = step.get("success", True)
        if tool in {"write_file", "delete_file"}:
            path = arguments.get("path")
            if path and success:
                is_delete = tool == "delete_file"
                log_file(path, "D" if is_delete else git_file_action(workspace, path))
                diff_text = metadata.get("diff")
                if diff_text:
                    renderer.render_diff(path, diff_text, title_prefix="DELETE" if is_delete else "PATCH")
        elif tool:
            log_step(f"{tool} {arguments}".strip())
        renderer.start_activity("Planning next move...")

    def respond(user_input: str, chat_state: ChatState) -> None:
        execute_intent = detect_execution_intent(user_input)

        if chat_state.mode == "plan" and execute_intent and last_plan:
            renderer.start_activity("Executing the previously agreed plan...")
            augmented_input = f"{user_input}\n\nPreviously agreed plan:\n{last_plan[0]}"
            try:
                reply = run_agent_turn(
                    model,
                    executor,
                    agent_task,
                    agent_memory,
                    workspace,
                    history,
                    augmented_input,
                    max_steps=DEVELOPER_MAX_STEPS,
                    require_execution_evidence=True,
                    on_step=on_step,
                )
            finally:
                renderer.finish_activity()
            memory.record_message(session_id, "user", user_input)
            memory.record_message(session_id, "assistant", reply)
            console.print(Text(reply))
            console.print("[dim](executed the agreed plan — mode is still \"plan\"; Shift+Tab to change it)[/dim]")
            return

        if chat_state.mode == "plan":
            log_step("Architect is analyzing (read-only)...")
            result = run_architect(model, workspace, user_input)
            last_plan[:] = [result]
            memory.record_message(session_id, "user", user_input)
            memory.record_message(session_id, "assistant", result)
            print_plan(
                result,
                note="Read-only — nothing changed. Cycle mode (Shift+Tab) to implement.",
            )
            return

        renderer.start_activity("Planning next move...")
        try:
            reply = run_agent_turn(
                model,
                executor,
                agent_task,
                agent_memory,
                workspace,
                history,
                user_input,
                max_steps=DEVELOPER_MAX_STEPS,
                require_execution_evidence=execute_intent,
                on_step=on_step,
            )
        finally:
            renderer.finish_activity()
        memory.record_message(session_id, "user", user_input)
        memory.record_message(session_id, "assistant", reply)
        console.print(Text(reply))

    memory_state = (
        "ready · continuity available"
        if memory.list_notes(str(workspace.path), limit=1)
        else "ready · empty"
    )
    print_startup_dashboard(
        workspace,
        model.name,
        model.is_available(),
        git_worktree_state(workspace),
        f"{model.engine} / {state.mode}",
        memory_state,
    )
    print_command_map(generate_command_map(typer.main.get_command(app)), compact=True)
    try:
        run_chat_loop(respond, state=state, workspace=workspace)
    finally:
        # Only worth a note if something actually happened — not for a two-message
        # "hi"/"hello" exchange. len(history) counts message-pairs from completed
        # normal/auto-mode turns (plan-mode turns are intentionally stateless).
        if len(history) >= 4 or touched_files:
            transcript = "\n".join(f"{m['role']}: {m['content']}" for m in history)
            try:
                note = run_summarizer(model, workspace, "Interactive chat session", transcript)
                memory.record_note(session_id, str(workspace.path), note)
                append_journal_entry(workspace, "Interactive chat session", note)
            except Exception:  # noqa: BLE001 - never let note-writing crash the exit path
                pass
        memory.close()


@app.command()
def commands() -> None:
    """List every available `skytrap` command with a one-line description."""
    click_command = typer.main.get_command(app)
    print_command_map(generate_command_map(click_command))


_DOCTOR_SYMBOL = {HEALTHY: ("✓", "green"), DEGRADED: ("⚠", "yellow")}


@app.command()
def doctor(fix_plan: bool = typer.Option(False, "--fix-plan", help="Print OS-specific remediation commands without executing them.")) -> None:
    """Health check every part the autonomous runtime depends on: Ollama, git,
    ripgrep, Tree-sitter, ast-grep, LSP servers, task-state storage, workspace
    permissions, the tool registry, decision parsing, the patch engine, and
    verification-command discovery. Every line reflects a real probe, not an
    assumption."""
    workspace = detect_workspace()
    report = run_doctor(workspace)
    for check in report.checks:
        symbol, style = _DOCTOR_SYMBOL.get(check.status, ("✗", "red"))
        console.print(f"[{style}]{symbol}[/{style}] [bold]{check.name}[/bold] — {check.detail}")
        if check.recommendation:
            console.print(f"    [dim]{check.recommendation}[/dim]")
    overall_symbol, overall_style = _DOCTOR_SYMBOL.get(report.overall, ("✗", "red"))
    console.print(
        f"\n[{overall_style}]{overall_symbol} Overall: {report.overall}[/{overall_style}]"
    )
    if report.ollama is not None:
        console.print("\n[bold]Ollama layers[/bold]")
        for label, value in (
            ("binary", report.ollama.binary_present), ("daemon", report.ollama.daemon_accessible),
            ("API", report.ollama.api_accessible), ("configured model", report.ollama.model_present),
            ("model load", report.ollama.model_loadable), ("minimal generation", report.ollama.generation_working),
        ):
            console.print(f"  {'✓' if value else '✗'} {label}")
    if report.lsp_servers:
        console.print("\n[bold]LSP matrix[/bold]")
        for server in report.lsp_servers:
            tested = ", ".join(server.capabilities_tested) or "none"
            console.print(f"  {server.language:<22} {server.server:<28} {server.status:<11} reachable={server.reachable} tested={tested}")
    console.print(f"[bold]Software Readiness: {report.software_readiness}/10[/bold]")
    console.print(f"[bold]Environment Readiness: {report.environment_readiness}/10[/bold]")
    console.print("\n[bold cyan]SkyTrap Intelligence Health Report[/bold cyan]")
    for dimension in intelligence_health_report(report):
        console.print(f"  {dimension.name:<28} {dimension.score}/10  [dim]{dimension.evidence}[/dim]")
    if fix_plan:
        console.print("\n[bold yellow]Fix plan (commands are suggestions only; none were executed)[/bold yellow]")
        items = build_fix_plan(report)
        if not items:
            console.print("  No remediation required.")
        for item in items:
            console.print(f"  [bold]{item.capability}[/bold] — {item.reason}\n    [cyan]{item.command}[/cyan]")
    if report.overall != HEALTHY:
        raise typer.Exit(1)


@app.command()
def bench(
    target: str | None = typer.Argument(None, help="Optional benchmark target (`models`)."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Run the local SkyTrap engineering-agent benchmark fixtures."""
    from skytrap.bench import SkyTrapBench

    if target is not None:
        if target != "models":
            console.print(f"[red]Unknown benchmark target: {target}[/red]")
            raise typer.Exit(2)
        from skytrap.models.ollama import OllamaHealthStatus, probe_ollama
        from skytrap.models.qualification import ModelQualificationSuite

        health = probe_ollama()
        if health.status != OllamaHealthStatus.HEALTHY:
            console.print(f"[red]{health.status.value}: {health.detail}[/red]")
            raise typer.Exit(1)
        result = ModelQualificationSuite().run(OllamaProvider())
        if json_output:
            typer.echo(result.model_dump_json(indent=2))
        else:
            console.print("[bold cyan]🐇 SKYTRAP MODEL QUALIFICATION[/bold cyan]")
            console.print(result.model_dump_json(indent=2))
        if not result.qualified:
            raise typer.Exit(1)
        return

    report = SkyTrapBench().run()
    if json_output:
        typer.echo(report.model_dump_json(indent=2))
        return
    console.print("[bold cyan]🐇 SKYTRAP BENCH[/bold cyan]")
    for scenario in report.scenarios:
        mark = "✓" if scenario.passed else "✗"
        style = "green" if scenario.passed else "red"
        console.print(f"[{style}]{mark}[/{style}] {scenario.name} · {scenario.duration_ms} ms")
    console.print(f"\nSuccess rate: [bold]{report.success_rate:.0%}[/bold] · {report.duration_ms} ms")
    if report.success_rate < 1:
        raise typer.Exit(1)


@update_app.command("check")
def update_check(json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON.")) -> None:
    """Check official registries for relevant updates; never installs anything."""
    from skytrap.technology.watch import TechnologyWatch

    report = TechnologyWatch().check()
    if json_output:
        typer.echo(report.model_dump_json(indent=2))
        return
    console.print("[bold cyan]╭─ 🐇 RABBIT SCOUT ─ Technology intelligence[/bold cyan]")
    current_category = None
    for finding in report.findings:
        if finding.category != current_category:
            current_category = finding.category
            console.print(f"\n[bold]{current_category.value.upper()}[/bold]")
        up_to_date = finding.recommendation.startswith("keep")
        symbol, style = ("✓", "green") if up_to_date else ("⚠", "yellow")
        versions = f"{finding.current_version or 'not installed'}"
        if finding.available_version:
            versions += f" → {finding.available_version}"
        console.print(f"[{style}]{symbol}[/{style}] {finding.technology}  {versions}  [bold]{finding.status.value}[/bold]")
        console.print(f"  [dim]{finding.recommendation} · source: {finding.source}[/dim]")
        if finding.category.value == "models":
            console.print(
                f"  [dim]hardware fit: {finding.hardware_fit or 'unknown'} · "
                f"expected benefit: {finding.expected_benefit or 'none established'} · "
                f"benchmark required: {'yes' if finding.benchmark_required else 'no'}[/dim]"
            )
    kept = sum(item.recommendation.startswith("keep") for item in report.findings)
    console.print(
        f"\nRecommendation: benchmark {report.benchmark_candidates} model candidate(s); "
        f"upgrade {report.upgrade_candidates} development tool(s); keep {kept} unchanged."
    )


@app.command()
def plan(task: str) -> None:
    """Analyze TASK against this workspace and print an implementation plan.
    Read-only: the Architect role has no write_file/shell/run_tests access, so
    nothing in the workspace is changed."""
    workspace = detect_workspace()
    model = OllamaProvider()
    log_step("Architect is analyzing the workspace...")
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
        print_project_detected(inspect_project(workspace))
        log_step("Architect is analyzing the workspace...")
        plan_text = run_architect(model, workspace, task, memory=memory)
        print_plan(plan_text, note="")

        if not confirm_implement_plan():
            console.print("[dim]Cancelled — nothing was changed.[/dim]")
            return

        touched_files: list[str] = []
        tools = _build_full_toolset(
            workspace, on_write=touched_files.append, on_delete=touched_files.append, memory=memory
        )
        renderer = AgentRenderer()

        def approval_callback(request: ApprovalRequest) -> bool:
            return _approve_autonomous_action(request, renderer)

        current_task = task
        for attempt in range(1, MAX_AUTOFIX_ATTEMPTS + 1):
            log_step(f"Developer is implementing the plan... ({attempt}/{MAX_AUTOFIX_ATTEMPTS})")
            summary = run_developer(
                model, tools, workspace, current_task, plan_text, approval_callback=approval_callback
            )

            log_step("Running the test suite...")
            test_result = RunTestsTool().execute(workspace, {})

            if test_result.success or attempt == MAX_AUTOFIX_ATTEMPTS:
                break
            console.print(
                f"[yellow]⚠ Tests failed — diagnosing and retrying "
                f"({attempt}/{MAX_AUTOFIX_ATTEMPTS})...[/yellow]"
            )
            current_task = (
                f"{task}\n\nAttempt {attempt} failed the test suite:\n{test_result.output}\n"
                "Fix the code so the tests pass."
            )

        print_developer_summary(summary)
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

        log_step("Reviewer is analyzing the diff...")
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

        created = [p for p in touched_files if git_file_action(workspace, p) == "A"]
        modified = [p for p in touched_files if p not in created]
        print_task_report(created, modified, test_result.success, summary)

        # End-of-task boundary: this is where a note is genuinely worth writing —
        # a completed build, not a cancelled or no-op one.
        outcome = f"Developer summary:\n{summary}\n\nReviewer findings:\n{review}"
        try:
            note = run_summarizer(model, workspace, task, outcome)
            memory.record_note(session_id, str(workspace.path), note)
            append_journal_entry(workspace, task, note)
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
