import json
import os
import re
import subprocess

from skytrap.core.language_detection import detect_languages
from skytrap.security.context import SecurityContext
from skytrap.security.findings import ScanOutcome, SecurityFinding
from skytrap.tools.filesystem import IGNORED_DIRS

SCANNER_NAME = "static_analysis"
MAX_FILES_SCANNED = 5_000
TIMEOUT_SECONDS = 180

# (extension, pattern, title, severity, cwe, remediation) — dangerous-pattern
# detection, not full taint analysis: high-signal, language-specific constructs
# that are worth a human's attention, each anchored to a real file:line match.
_PATTERN_RULES: list[tuple[str, re.Pattern, str, str, str, str]] = [
    # Python
    (".py", re.compile(r"\beval\s*\("), "Use of eval()", "high", "CWE-95",
     "Avoid eval() on any input that isn't fully trusted and static; use ast.literal_eval for data."),
    (".py", re.compile(r"\bexec\s*\("), "Use of exec()", "high", "CWE-95",
     "Avoid exec() on dynamic/untrusted strings; restructure to avoid dynamic code execution."),
    (".py", re.compile(r"\bos\.system\s*\("), "Use of os.system()", "high", "CWE-78",
     "Use subprocess.run([...]) with a list of args (no shell=True) instead of os.system()."),
    (".py", re.compile(r"subprocess\.\w+\([^)]*shell\s*=\s*True"), "subprocess with shell=True", "high", "CWE-78",
     "Pass a list of arguments and shell=False (the default) instead of a shell string."),
    (".py", re.compile(r"\bpickle\.loads?\s*\("), "Unsafe pickle deserialization", "high", "CWE-502",
     "Never unpickle data from an untrusted source; use JSON or a schema-validated format instead."),
    (".py", re.compile(r"yaml\.load\s*\((?!.*Loader\s*=\s*yaml\.SafeLoader)"), "yaml.load without SafeLoader",
     "medium", "CWE-502", "Use yaml.safe_load() (or Loader=yaml.SafeLoader) instead of the default loader."),
    (".py", re.compile(r"\.format\s*\([^)]*\)\s*%|%\s*\([^)]*\)\s*.*(SELECT|INSERT|UPDATE|DELETE)\b", re.I),
     "Possible string-built SQL", "high", "CWE-89",
     "Use parameterized queries (?, %s placeholders) instead of building SQL with string formatting."),
    # JavaScript / TypeScript
    (".js", re.compile(r"\beval\s*\("), "Use of eval()", "high", "CWE-95",
     "Avoid eval() on dynamic input; use JSON.parse for data or restructure the logic."),
    (".ts", re.compile(r"\beval\s*\("), "Use of eval()", "high", "CWE-95",
     "Avoid eval() on dynamic input; use JSON.parse for data or restructure the logic."),
    (".js", re.compile(r"child_process\.exec\s*\("), "child_process.exec with a shell", "high", "CWE-78",
     "Use execFile/spawn with an argument array instead of exec(), which invokes a shell."),
    (".ts", re.compile(r"child_process\.exec\s*\("), "child_process.exec with a shell", "high", "CWE-78",
     "Use execFile/spawn with an argument array instead of exec(), which invokes a shell."),
    (".js", re.compile(r"\.innerHTML\s*="), "Direct innerHTML assignment", "medium", "CWE-79",
     "Use textContent for plain text, or a sanitizer (e.g. DOMPurify) before setting HTML."),
    (".ts", re.compile(r"\.innerHTML\s*="), "Direct innerHTML assignment", "medium", "CWE-79",
     "Use textContent for plain text, or a sanitizer (e.g. DOMPurify) before setting HTML."),
    (".js", re.compile(r"dangerouslySetInnerHTML"), "dangerouslySetInnerHTML usage", "medium", "CWE-79",
     "Sanitize the HTML (e.g. DOMPurify) before passing it to dangerouslySetInnerHTML."),
    (".ts", re.compile(r"dangerouslySetInnerHTML"), "dangerouslySetInnerHTML usage", "medium", "CWE-79",
     "Sanitize the HTML (e.g. DOMPurify) before passing it to dangerouslySetInnerHTML."),
    # C / C++
    (".c", re.compile(r"\b(strcpy|strcat|sprintf|gets)\s*\("), "Unsafe C string function", "high", "CWE-120",
     "Use bounded variants (strncpy/strncat/snprintf) or a safer string abstraction."),
    (".cpp", re.compile(r"\b(strcpy|strcat|sprintf|gets)\s*\("), "Unsafe C string function", "high", "CWE-120",
     "Use bounded variants (strncpy/strncat/snprintf) or a safer string abstraction."),
    # Rust
    (".rs", re.compile(r"\bunsafe\s*\{"), "unsafe block", "info", "CWE-119",
     "Review this unsafe block's invariants explicitly; keep unsafe surface as small as possible."),
    # Ruby
    (".rb", re.compile(r"\bsystem\s*\(|`[^`]*#\{"), "Shell command execution / interpolation", "high", "CWE-78",
     "Avoid interpolating untrusted input into system()/backticks; use an argument array."),
    (".rb", re.compile(r"\.permit!\b"), "Rails mass assignment via permit!", "medium", "CWE-915",
     "Explicitly list permitted attributes instead of permit! (which allows everything)."),
]


def _run_pattern_scan(context: SecurityContext, extensions: set[str]) -> list[SecurityFinding]:
    root = context.workspace.path
    findings: list[SecurityFinding] = []
    scanned = 0

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        for filename in filenames:
            ext = os.path.splitext(filename)[1]
            if ext not in extensions:
                continue
            if scanned >= MAX_FILES_SCANNED:
                return findings
            scanned += 1
            path = os.path.join(dirpath, filename)
            try:
                content = open(path, encoding="utf-8", errors="strict").read()
            except (UnicodeDecodeError, OSError):
                continue
            rel = os.path.relpath(path, root)

            for rule_ext, pattern, title, severity, cwe, remediation in _PATTERN_RULES:
                if rule_ext != ext:
                    continue
                for match in pattern.finditer(content):
                    line = content.count("\n", 0, match.start()) + 1
                    findings.append(
                        SecurityFinding(
                            id=f"static-{rel}-{line}-{title}",
                            title=title,
                            category="source_code",
                            severity=severity,
                            confidence="medium",
                            impact=f"Dangerous pattern found — actual exploitability depends on whether "
                            f"attacker-controlled data reaches this code path.",
                            remediation=remediation,
                            scanner="static_analysis:builtin",
                            file=rel,
                            line=line,
                            evidence=match.group(0)[:120],
                            cwe=cwe,
                            verified="unverified",
                        )
                    )
    return findings


def _run_bandit(context: SecurityContext, bandit_path: str) -> list[SecurityFinding] | None:
    try:
        result = subprocess.run(
            [bandit_path, "-r", str(context.workspace.path), "-f", "json"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if not result.stdout.strip():
        return None
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return None

    severity_map = {"LOW": "low", "MEDIUM": "medium", "HIGH": "high"}
    confidence_map = {"LOW": "low", "MEDIUM": "medium", "HIGH": "high"}
    findings = []
    for issue in data.get("results", []):
        findings.append(
            SecurityFinding(
                id=f"bandit-{issue.get('test_id')}-{issue.get('filename')}-{issue.get('line_number')}",
                title=issue.get("issue_text", "Bandit finding"),
                category="source_code",
                severity=severity_map.get(issue.get("issue_severity", "MEDIUM"), "medium"),
                confidence=confidence_map.get(issue.get("issue_confidence", "MEDIUM"), "medium"),
                impact=issue.get("issue_text", ""),
                remediation=f"See Bandit rule {issue.get('test_id')} ({issue.get('test_name')}).",
                scanner="static_analysis:bandit",
                file=issue.get("filename"),
                line=issue.get("line_number"),
                cwe=f"CWE-{issue.get('issue_cwe', {}).get('id')}" if issue.get("issue_cwe") else None,
                verified="likely",
            )
        )
    return findings


def scan(context: SecurityContext, toolchain: dict[str, str | None]) -> ScanOutcome:
    languages = detect_languages(context.workspace)
    if not languages:
        return ScanOutcome(scanner=SCANNER_NAME, ran=False, skipped_reason="no recognized language in workspace")

    findings: list[SecurityFinding] = []
    detail_parts: list[str] = []

    if "python" in {m.profile.id for m in languages} and toolchain.get("bandit"):
        bandit_findings = _run_bandit(context, toolchain["bandit"])
        if bandit_findings is not None:
            findings.extend(bandit_findings)
            detail_parts.append("bandit")

    extensions = {ext for m in languages for ext in m.profile.extensions}
    findings.extend(_run_pattern_scan(context, extensions))
    detail_parts.append("built-in patterns")

    return ScanOutcome(scanner=SCANNER_NAME, ran=True, findings=findings, detail=", ".join(detail_parts))
