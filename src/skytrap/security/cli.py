from pathlib import Path

import typer

from skytrap.core.context import detect_workspace
from skytrap.security.context import AuthorizationScope, SecurityContext
from skytrap.security.engine import REPOSITORY_SCANNERS, run_repository_audit
from skytrap.security.report import SecurityReport
from skytrap.security.scanners import dependencies as dependencies_scanner
from skytrap.security.scanners import dns_scan, headers, network, secrets, static_analysis, tls_scan
from skytrap.security.toolchain import detect_security_toolchain
from skytrap.ui.security_terminal import (
    console,
    print_security_banner,
    print_security_report,
    print_security_step,
    print_security_toolchain,
)

security_app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    help="Security Engineer / AppSec / Network / Secure Code Review mode — audit, "
    "detect, remediate. Defensive-only: never turns a finding into exploitation.",
)

_NOT_YET_IMPLEMENTED = (
    "not yet implemented in this MVP — currently available: audit, code, secrets, "
    "dependencies, headers, dns, tls, network"
)


@security_app.callback(invoke_without_command=True)
def security_main(ctx: typer.Context) -> None:
    """`skytrap security` with no subcommand behaves like `skytrap security audit .`"""
    if ctx.invoked_subcommand is not None:
        return
    _run_audit(".")


def _run_audit(path: str, ci: bool = False, fail_threshold: str = "high") -> SecurityReport:
    workspace = detect_workspace(Path(path))
    context = SecurityContext(workspace=workspace, scope=AuthorizationScope(), ci_mode=ci)

    print_security_banner(str(workspace.path))
    total = len(REPOSITORY_SCANNERS)

    def on_step(name: str, status: str) -> None:
        index = [n for n, _ in REPOSITORY_SCANNERS].index(name) + 1
        print_security_step(name, status, index, total)

    report = run_repository_audit(context, on_step=on_step)
    print_security_report(report)

    if ci and report.fails_threshold(fail_threshold):
        console.print(f"\n[bold red]✗ CI threshold '{fail_threshold}' exceeded[/bold red]")
        raise typer.Exit(1)
    return report


@security_app.command(name="audit")
def audit_cmd(
    path: str = typer.Argument(".", help="Repository path to audit."),
    ci: bool = typer.Option(False, "--ci", help="Exit non-zero if findings meet/exceed --fail-threshold."),
    fail_threshold: str = typer.Option("high", "--fail-threshold", help="critical|high|medium|low"),
) -> None:
    """Full defensive repository audit: secrets, dependencies, static analysis."""
    _run_audit(path, ci=ci, fail_threshold=fail_threshold)


@security_app.command(name="code")
def code_cmd(path: str = typer.Argument(".", help="Path to review.")) -> None:
    """Static code analysis only (dangerous patterns, language-aware)."""
    workspace = detect_workspace(Path(path))
    context = SecurityContext(workspace=workspace, scope=AuthorizationScope())
    toolchain = detect_security_toolchain()
    print_security_banner(str(workspace.path))
    outcome = static_analysis.scan(context, toolchain)
    print_security_report(SecurityReport(target=str(workspace.path), outcomes=[outcome]))


@security_app.command(name="secrets")
def secrets_cmd(path: str = typer.Argument(".", help="Path to scan.")) -> None:
    """Secret scanning only (gitleaks if installed, built-in patterns otherwise)."""
    workspace = detect_workspace(Path(path))
    context = SecurityContext(workspace=workspace, scope=AuthorizationScope())
    toolchain = detect_security_toolchain()
    print_security_banner(str(workspace.path))
    outcome = secrets.scan(context, toolchain)
    print_security_report(SecurityReport(target=str(workspace.path), outcomes=[outcome]))


@security_app.command(name="dependencies")
def dependencies_cmd(path: str = typer.Argument(".", help="Path to audit.")) -> None:
    """Dependency vulnerability audit (pip-audit/npm audit/cargo audit/... —
    whichever's installed for the detected language)."""
    workspace = detect_workspace(Path(path))
    context = SecurityContext(workspace=workspace, scope=AuthorizationScope())
    toolchain = detect_security_toolchain()
    print_security_banner(str(workspace.path))
    outcome = dependencies_scanner.scan(context, toolchain)
    print_security_report(SecurityReport(target=str(workspace.path), outcomes=[outcome]))


def _run_headers(url: str) -> None:
    print_security_banner(url, authorized_network=True)
    outcome = headers.scan_url(url)
    print_security_report(SecurityReport(target=url, outcomes=[outcome]))


@security_app.command(name="headers")
def headers_cmd(url: str = typer.Argument(..., help="URL to check, e.g. http://localhost:3000")) -> None:
    """HTTP security header check against an explicitly named URL."""
    _run_headers(url)


@security_app.command(name="web")
def web_cmd(url: str = typer.Argument(..., help="URL to check, e.g. http://localhost:3000")) -> None:
    """Defensive web-app check. MVP scope: HTTP security headers only — more OWASP
    categories (auth, sessions, CSRF, ...) are on the roadmap, not faked here."""
    _run_headers(url)


@security_app.command(name="dns")
def dns_cmd(domain: str = typer.Argument(..., help="Domain to analyze, e.g. example.com")) -> None:
    """DNS record + basic mail-security (SPF/DMARC) check against an explicitly named domain."""
    print_security_banner(domain, authorized_network=True)
    outcome = dns_scan.scan_domain(domain)
    print_security_report(SecurityReport(target=domain, outcomes=[outcome]))


@security_app.command(name="tls")
def tls_cmd(
    host: str = typer.Argument(..., help="Host to check, e.g. example.com"),
    port: int = typer.Option(443, "--port"),
) -> None:
    """TLS handshake + certificate check against an explicitly named host."""
    print_security_banner(f"{host}:{port}", authorized_network=True)
    outcome = tls_scan.scan_host(host, port)
    print_security_report(SecurityReport(target=f"{host}:{port}", outcomes=[outcome]))


@security_app.command(name="network")
def network_cmd(target: str = typer.Argument(..., help="CIDR to analyze, e.g. 192.168.1.0/24")) -> None:
    """Local CIDR/subnet analysis (pure math, sends no traffic). Active host
    discovery (Nmap) against a remote network isn't in this MVP."""
    print_security_banner(target)
    outcome = network.analyze_cidr(target)
    print_security_report(SecurityReport(target=target, outcomes=[outcome]))


@security_app.command(name="toolchain")
def toolchain_cmd() -> None:
    """List which security tools SkyTrap actually found installed on this machine."""
    print_security_toolchain(detect_security_toolchain())


def _make_stub(name: str):
    def _stub() -> None:
        console.print(f"[yellow]⚠ `skytrap security {name}` is {_NOT_YET_IMPLEMENTED}.[/yellow]")

    _stub.__name__ = f"{name.replace('-', '_')}_stub"
    _stub.__doc__ = f"Not yet implemented — {_NOT_YET_IMPLEMENTED}."
    return _stub


for _stub_name in ("container", "database", "permissions", "config", "report", "fix", "ssh"):
    security_app.command(name=_stub_name)(_make_stub(_stub_name))
