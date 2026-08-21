from typing import Callable

from skytrap.security.context import SecurityContext
from skytrap.security.report import SecurityReport
from skytrap.security.scanners import dependencies, secrets, static_analysis
from skytrap.security.toolchain import detect_security_toolchain

# Order matters for the progress display (item 44) — each step is a real scanner
# call, never a fabricated "done".
REPOSITORY_SCANNERS = (
    ("Secrets", secrets.scan),
    ("Dependencies", dependencies.scan),
    ("Static analysis", static_analysis.scan),
)


def run_repository_audit(
    context: SecurityContext, on_step: Callable[[str, str], None] | None = None
) -> SecurityReport:
    """The `skytrap security audit`/`skytrap security code` core loop: local,
    read-only, defensive scanners only — no network target involved, so this never
    needs anything beyond AuthorizationScope's default (local_repository=True).
    `on_step`, if given, is called with (step_name, status) as each scanner starts
    and finishes, for real (not simulated) progress output."""
    toolchain = detect_security_toolchain()
    report = SecurityReport(target=str(context.workspace.path))

    for name, scan_fn in REPOSITORY_SCANNERS:
        if on_step:
            on_step(name, "running")
        outcome = scan_fn(context, toolchain)
        report.outcomes.append(outcome)
        if on_step:
            on_step(name, "done" if outcome.ran else "skipped")

    return report
