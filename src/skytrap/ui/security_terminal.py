from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from skytrap.security.findings import ScanOutcome, SecurityFinding
from skytrap.security.report import SecurityReport

console = Console()

_SEVERITY_STYLE = {
    "critical": "bold red",
    "high": "red",
    "medium": "yellow",
    "low": "cyan",
    "info": "dim",
}


def print_security_banner(target: str, authorized_network: bool = False) -> None:
    body = (
        f"[bold]MODE[/bold]      Defensive Security Audit\n"
        f"[bold]TARGET[/bold]    {target}\n"
        f"[bold]AUTHORIZATION[/bold]  {'explicit network target' if authorized_network else 'local / user-controlled target'}\n"
        f"[bold]STATUS[/bold]    [magenta]● inspecting[/magenta]"
    )
    console.print(Panel(body, title="◆ SKYTRAP SECURITY", border_style="magenta", padding=(1, 2)))


def print_security_step(name: str, status: str, index: int, total: int) -> None:
    """Every call here corresponds to a scanner that actually started/finished/was
    skipped — never a fabricated progress line."""
    mark = {"running": "[magenta]●[/magenta]", "done": "[green]✓[/green]", "skipped": "[dim]…[/dim]"}[status]
    console.print(f"[{index}/{total}] {name:<20} {mark}")


def print_security_toolchain(toolchain: dict[str, str | None]) -> None:
    table = Table(title="Security toolchain", border_style="magenta", show_lines=False)
    table.add_column("Tool", style="bold cyan")
    table.add_column("Status")
    for name, path in sorted(toolchain.items()):
        status = "[green]✓ found[/green]" if path else "[dim]✗ not installed[/dim]"
        table.add_row(name, status)
    console.print(table)


def _finding_line(finding: SecurityFinding) -> str:
    style = _SEVERITY_STYLE[finding.severity]
    location = f"{finding.file}:{finding.line}" if finding.file and finding.line else (finding.file or finding.asset or "")
    header = f"[{style}]{finding.severity.upper():<8}[/{style}] [dim](confidence: {finding.confidence})[/dim] {finding.title}"
    lines = [header]
    if location:
        lines.append(f"    [cyan]{location}[/cyan]")
    if finding.evidence:
        lines.append(f"    evidence: {finding.evidence}")
    lines.append(f"    impact: {finding.impact}")
    lines.append(f"    remediation: {finding.remediation}")
    if finding.cwe or finding.cve:
        lines.append(f"    ref: {', '.join(x for x in (finding.cwe, finding.cve) if x)}")
    lines.append(f"    status: {finding.verified}")
    return "\n".join(lines)


def print_security_report(report: SecurityReport) -> None:
    counts = report.counts_by_severity()
    summary = "\n".join(
        f"[{_SEVERITY_STYLE[sev]}]{sev.capitalize():<9}[/{_SEVERITY_STYLE[sev]}] {count}"
        for sev, count in counts.items()
    )
    console.print(
        Panel(
            summary,
            title=f"◆ SKYTRAP SECURITY REPORT — {report.target}",
            border_style="magenta",
            padding=(1, 2),
        )
    )

    for outcome in report.outcomes:
        status = "[green]✓[/green]" if outcome.ran else "[dim]…[/dim]"
        detail = outcome.detail or outcome.skipped_reason or ""
        console.print(f"{status} {outcome.scanner:<20} {len(outcome.findings)} finding(s)  [dim]{detail}[/dim]")

    findings = report.findings
    if not findings:
        console.print("\n[green]✓ No findings — nothing to remediate from this audit.[/green]")
        return

    console.print()
    for finding in findings:
        console.print(Panel(Text.from_markup(_finding_line(finding)), border_style=_SEVERITY_STYLE[finding.severity].split()[-1]))
