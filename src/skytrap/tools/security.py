from skytrap.core.context import WorkspaceContext
from skytrap.security.context import AuthorizationScope, SecurityContext
from skytrap.security.engine import run_repository_audit
from skytrap.tools.base import Tool, ToolResult


class SecurityAuditTool(Tool):
    name = "security_audit"
    description = (
        "Run a defensive, local, read-only security audit of the workspace: secret "
        "scanning, dependency vulnerability scanning, and static analysis for "
        "dangerous code patterns (eval/exec, shell injection, unsafe deserialization, "
        "hardcoded credentials, etc). Never sends network traffic and never modifies "
        "the workspace. Reports findings with severity/confidence/evidence/"
        "remediation — never claims a vulnerability is confirmed without evidence. "
        "No arguments."
    )

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        context = SecurityContext(workspace=workspace, scope=AuthorizationScope())
        report = run_repository_audit(context)

        counts = report.counts_by_severity()
        lines = [
            f"Security audit of {workspace.path}:",
            ", ".join(f"{sev}: {n}" for sev, n in counts.items()),
            "",
        ]
        for outcome in report.outcomes:
            status = "ran" if outcome.ran else f"skipped ({outcome.skipped_reason})"
            lines.append(f"- {outcome.scanner}: {status}, {len(outcome.findings)} finding(s)")

        if report.findings:
            lines.append("")
            lines.append("Findings:")
            for finding in report.findings:
                location = f" ({finding.file}:{finding.line})" if finding.file and finding.line else ""
                lines.append(
                    f"- [{finding.severity.upper()}/{finding.confidence} confidence] "
                    f"{finding.title}{location} — {finding.remediation}"
                )

        return ToolResult(success=True, output="\n".join(lines))
