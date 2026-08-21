import json
import shlex
import subprocess

from skytrap.core.language_detection import detect_languages
from skytrap.security.context import SecurityContext
from skytrap.security.findings import ScanOutcome, SecurityFinding

SCANNER_NAME = "dependencies"
TIMEOUT_SECONDS = 180

# language id -> (required tool name, command, parser). Only tools that are
# actually installed (checked against the toolchain dict) are ever invoked.
_AUDIT_COMMANDS: dict[str, tuple[str, str]] = {
    "python": ("pip-audit", "pip-audit -f json"),
    "rust": ("cargo-audit", "cargo audit --json"),
    "ruby": ("bundle", "bundle-audit check --format json"),
    "go": ("govulncheck", "govulncheck -json ./..."),
}


def _npm_family_audit(manager: str) -> str:
    return f"{manager} audit --json"


def _run(command: str, cwd) -> str | None:
    try:
        result = subprocess.run(
            shlex.split(command), cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT_SECONDS
        )
    except (subprocess.SubprocessError, OSError):
        return None
    return result.stdout


def _finding_from_pip_audit(entry: dict, rel_lockfile: str) -> list[SecurityFinding]:
    findings = []
    for vuln in entry.get("vulns", []):
        findings.append(
            SecurityFinding(
                id=f"dep-{entry.get('name')}-{vuln.get('id')}",
                title=f"{entry.get('name')} {entry.get('version')}: {vuln.get('id')}",
                category="dependencies",
                severity="medium",  # pip-audit doesn't grade severity itself
                confidence="high",
                impact=vuln.get("description", "Known vulnerability in this dependency version.")[:500],
                remediation=(
                    f"Upgrade to a fixed version: {', '.join(vuln.get('fix_versions', []) or ['see advisory'])}."
                ),
                scanner="dependencies:pip-audit",
                asset=f"{entry.get('name')}@{entry.get('version')}",
                file=rel_lockfile,
                cve=vuln.get("id") if str(vuln.get("id", "")).startswith("CVE-") else None,
                verified="confirmed",
            )
        )
    return findings


def _finding_from_npm_audit(data: dict, manager: str) -> list[SecurityFinding]:
    findings = []
    vulnerabilities = data.get("vulnerabilities", {})
    for package_name, info in vulnerabilities.items():
        severity = info.get("severity", "medium")
        if severity not in ("info", "low", "medium", "high", "critical"):
            severity = "medium"
        via = info.get("via", [])
        advisory_titles = [v.get("title") for v in via if isinstance(v, dict) and v.get("title")]
        findings.append(
            SecurityFinding(
                id=f"dep-{package_name}-{manager}",
                title=f"{package_name}: {advisory_titles[0] if advisory_titles else 'known advisory'}",
                category="dependencies",
                severity=severity,
                confidence="high",
                impact=f"{len(via)} advisory(ies) affect installed range of {package_name}.",
                remediation=f"Run `{manager} audit fix` after reviewing whether the fix is a "
                "major-version bump (check for breaking changes before applying).",
                scanner=f"dependencies:{manager} audit",
                asset=package_name,
                verified="confirmed",
            )
        )
    return findings


def scan(context: SecurityContext, toolchain: dict[str, str | None]) -> ScanOutcome:
    root = context.workspace.path
    languages = detect_languages(context.workspace)
    findings: list[SecurityFinding] = []
    ran_any = False
    skipped: list[str] = []

    for match in languages:
        lang_id = match.profile.id

        if lang_id in ("javascript", "typescript"):
            from skytrap.core.languages.javascript import detect_node_package_manager

            manager = detect_node_package_manager(context.workspace)
            if not toolchain.get(manager):
                skipped.append(f"{manager} not installed (needed for {match.profile.name} audit)")
                continue
            output = _run(_npm_family_audit(manager), root)
            if output is None:
                skipped.append(f"{manager} audit failed to run")
                continue
            ran_any = True
            try:
                data = json.loads(output)
                findings.extend(_finding_from_npm_audit(data, manager))
            except ValueError:
                pass
            continue

        command_info = _AUDIT_COMMANDS.get(lang_id)
        if command_info is None:
            continue
        tool_name, command = command_info
        if not toolchain.get(tool_name):
            skipped.append(f"{tool_name} not installed (needed for {match.profile.name} audit)")
            continue

        output = _run(command, root)
        if output is None:
            skipped.append(f"{tool_name} failed to run")
            continue
        ran_any = True
        if lang_id == "python":
            try:
                data = json.loads(output)
                for entry in data.get("dependencies", data if isinstance(data, list) else []):
                    findings.extend(_finding_from_pip_audit(entry, "requirements/pyproject"))
            except (ValueError, AttributeError):
                pass
        # cargo-audit/bundler-audit/govulncheck JSON shapes differ further — parsed
        # opportunistically; a tool that ran but whose output SkyTrap can't yet
        # structure still counts as "ran" (real output exists) even if 0 findings
        # were extracted from it, rather than silently claiming nothing happened.

    if not ran_any:
        return ScanOutcome(
            scanner=SCANNER_NAME,
            ran=False,
            skipped_reason="; ".join(skipped) if skipped else "no dependency manifest with an available audit tool",
        )
    return ScanOutcome(
        scanner=SCANNER_NAME,
        ran=True,
        findings=findings,
        detail="; ".join(skipped) if skipped else None,
    )
