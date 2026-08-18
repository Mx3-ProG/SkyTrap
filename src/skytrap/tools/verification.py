import json
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from skytrap.core.context import WorkspaceContext
from skytrap.tools.base import Tool, ToolResult
from skytrap.tools.filesystem import resolve_in_workspace

LOCAL_HOSTS = {"localhost", "127.0.0.1"}
AUDIT_TIMEOUT_SECONDS = 120
MAX_FAILING_AUDITS = 10


def _validate_local_url(url: str) -> tuple[bool, str]:
    """Restricts audits to a local dev server. Without this, an audit tool with no
    confirmation gate (SAFE) would let the model probe arbitrary network hosts —
    the same reasoning as the workspace path sandbox, applied to URLs.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, f"URL must start with http:// or https:// (got: {url!r})"
    if parsed.hostname not in LOCAL_HOSTS:
        return False, (
            f"Only localhost/127.0.0.1 URLs are allowed (got host {parsed.hostname!r}) — "
            "this audits your local dev server, not arbitrary network hosts"
        )
    return True, ""


class LighthouseAuditTool(Tool):
    name = "lighthouse_audit"
    description = (
        "Run a Lighthouse audit (Performance, Accessibility, Best Practices, SEO) "
        "against a URL already being served by a local dev server. The dev server "
        "must already be running (this tool cannot start one). "
        'Arguments: {"url": "<http://localhost:PORT/...>"}'
    )

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        url = arguments.get("url")
        if not url:
            return ToolResult(success=False, output="Missing required argument 'url'")

        ok, error = _validate_local_url(url)
        if not ok:
            return ToolResult(success=False, output=error)

        try:
            result = subprocess.run(
                [
                    "npx",
                    "--yes",
                    "lighthouse",
                    url,
                    "--output=json",
                    "--output-path=stdout",
                    "--quiet",
                    "--chrome-flags=--headless=new",
                ],
                cwd=workspace.path,
                capture_output=True,
                text=True,
                timeout=AUDIT_TIMEOUT_SECONDS,
            )
        except FileNotFoundError:
            return ToolResult(success=False, output="npx is not installed (needs Node.js)")
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False, output=f"Lighthouse audit timed out after {AUDIT_TIMEOUT_SECONDS}s"
            )
        except subprocess.SubprocessError as exc:
            return ToolResult(success=False, output=f"Lighthouse failed to run: {exc}")

        if result.returncode != 0:
            return ToolResult(
                success=False,
                output=f"Lighthouse error (exit {result.returncode}):\n{result.stderr.strip()[-2000:]}",
            )

        try:
            report = json.loads(result.stdout)
        except json.JSONDecodeError:
            return ToolResult(
                success=False, output=f"Could not parse Lighthouse output:\n{result.stdout[-2000:]}"
            )

        return ToolResult(success=True, output=_summarize_lighthouse_report(report))


def _summarize_lighthouse_report(report: dict) -> str:
    """Lighthouse's raw JSON report is 100KB+ — far too much for a small model's
    context. This extracts just the category scores and the worst-scoring audits.
    """
    lines = []

    categories = report.get("categories", {})
    for category in categories.values():
        score = category.get("score")
        score_display = f"{round(score * 100)}/100" if score is not None else "N/A"
        lines.append(f"{category.get('title', '?')}: {score_display}")

    audits = report.get("audits", {})
    failing = [
        audit
        for audit in audits.values()
        if isinstance(audit.get("score"), (int, float)) and audit["score"] < 0.9
    ]
    failing.sort(key=lambda audit: audit["score"])

    if failing:
        lines.append("\nTop issues:")
        for audit in failing[:MAX_FAILING_AUDITS]:
            lines.append(f"- {audit.get('title', '?')} (score: {round(audit['score'] * 100)}/100)")

    return "\n".join(lines)


class AccessibilityCheckTool(Tool):
    name = "accessibility_check"
    description = (
        "Run an accessibility audit (axe-core) against a URL already being served by "
        "a local dev server, listing WCAG violations. The dev server must already be "
        'running. Arguments: {"url": "<http://localhost:PORT/...>"}'
    )

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        url = arguments.get("url")
        if not url:
            return ToolResult(success=False, output="Missing required argument 'url'")

        ok, error = _validate_local_url(url)
        if not ok:
            return ToolResult(success=False, output=error)

        try:
            result = subprocess.run(
                ["npx", "--yes", "@axe-core/cli", url, "--save", "-"],
                cwd=workspace.path,
                capture_output=True,
                text=True,
                timeout=AUDIT_TIMEOUT_SECONDS,
            )
        except FileNotFoundError:
            return ToolResult(success=False, output="npx is not installed (needs Node.js)")
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                output=f"Accessibility check timed out after {AUDIT_TIMEOUT_SECONDS}s",
            )
        except subprocess.SubprocessError as exc:
            return ToolResult(success=False, output=f"axe-core failed to run: {exc}")

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            # axe-core CLI prints progress text to stdout in some versions even with
            # --save -; fall back to raw output rather than pretending we parsed it.
            combined = (result.stdout + "\n" + result.stderr).strip()
            return ToolResult(success=result.returncode == 0, output=combined[-2000:] or "(no output)")

        return ToolResult(success=True, output=_summarize_axe_report(data))


def _summarize_axe_report(data: list | dict) -> str:
    # @axe-core/cli --save - outputs a list of per-URL results
    results = data if isinstance(data, list) else [data]
    violations = []
    for entry in results:
        violations.extend(entry.get("violations", []))

    if not violations:
        return "No accessibility violations found."

    lines = [f"{len(violations)} accessibility violation(s):"]
    for violation in violations:
        nodes = violation.get("nodes", [])
        target = nodes[0].get("target", ["?"])[0] if nodes else "?"
        lines.append(
            f"- [{violation.get('impact', '?')}] {violation.get('id', '?')}: "
            f"{violation.get('help', '?')} ({target})"
        )
    return "\n".join(lines)


class HtmlLintTool(Tool):
    name = "html_lint"
    description = (
        "Lint an HTML file or directory with htmlhint. "
        'Arguments: {"path": "<path relative to workspace root>"}'
    )

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        return _run_lint(workspace, arguments, "htmlhint")


_STYLELINT_CONFIG_FILES = (
    ".stylelintrc",
    ".stylelintrc.json",
    ".stylelintrc.yaml",
    ".stylelintrc.yml",
    ".stylelintrc.js",
    ".stylelintrc.cjs",
)

# Unlike htmlhint (which has sane built-in defaults), stylelint refuses to run at all
# without a config — most scratch/test projects won't have one, so we fall back to a
# small hand-picked rule set rather than requiring the user to set one up first.
_DEFAULT_STYLELINT_CONFIG = json.dumps(
    {
        "rules": {
            "block-no-empty": True,
            "color-no-invalid-hex": True,
            "declaration-block-no-duplicate-properties": True,
            "no-duplicate-selectors": True,
            "no-empty-source": True,
            "no-invalid-position-at-import-rule": True,
            "property-no-unknown": True,
            "unit-no-unknown": True,
        }
    }
)


def _has_stylelint_config(workspace: WorkspaceContext) -> bool:
    if any((workspace.path / name).exists() for name in _STYLELINT_CONFIG_FILES):
        return True
    package_json = workspace.path / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        return "stylelint" in data
    return False


class CssLintTool(Tool):
    name = "css_lint"
    description = (
        "Lint a CSS file or directory with stylelint. Uses the project's own "
        "stylelint config if it has one, otherwise a small built-in default rule set. "
        'Arguments: {"path": "<path relative to workspace root>"}'
    )

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        if _has_stylelint_config(workspace):
            return _run_lint(workspace, arguments, "stylelint")

        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as config_file:
            config_file.write(_DEFAULT_STYLELINT_CONFIG)
            config_path = config_file.name
        try:
            return _run_lint(workspace, arguments, "stylelint", extra_args=["--config", config_path])
        finally:
            Path(config_path).unlink(missing_ok=True)


def _run_lint(
    workspace: WorkspaceContext, arguments: dict, package: str, extra_args: list[str] | None = None
) -> ToolResult:
    path_arg = arguments.get("path")
    if not path_arg:
        return ToolResult(success=False, output="Missing required argument 'path'")

    ok, resolved = resolve_in_workspace(workspace, path_arg)
    if not ok:
        return ToolResult(success=False, output=resolved)
    if not Path(resolved).exists():
        return ToolResult(success=False, output=f"Path not found: {path_arg}")

    try:
        result = subprocess.run(
            ["npx", "--yes", package, *(extra_args or []), path_arg],
            cwd=workspace.path,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        return ToolResult(success=False, output="npx is not installed (needs Node.js)")
    except subprocess.TimeoutExpired:
        return ToolResult(success=False, output=f"{package} timed out")
    except subprocess.SubprocessError as exc:
        return ToolResult(success=False, output=f"{package} failed to run: {exc}")

    output = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode == 0:
        return ToolResult(success=True, output=output or "No issues found.")
    return ToolResult(success=False, output=output[-3000:] or f"{package} reported errors")
