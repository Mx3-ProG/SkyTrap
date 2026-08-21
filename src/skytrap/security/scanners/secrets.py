import os
import re
import subprocess

from skytrap.security.context import SecurityContext
from skytrap.security.findings import ScanOutcome, SecurityFinding
from skytrap.tools.filesystem import IGNORED_DIRS

SCANNER_NAME = "secrets"
MAX_FILE_BYTES = 500_000
MAX_FILES_SCANNED = 5_000

# (pattern, title, cwe) — conservative, high-signal patterns only; a generic
# "looks like a password" heuristic produces too many false positives to be useful,
# so this deliberately favors precision over recall for the built-in scanner.
_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key ID", "CWE-798"),
    (re.compile(r"sk_live_[0-9a-zA-Z]{16,}"), "Stripe live secret key", "CWE-798"),
    (re.compile(r"ghp_[0-9a-zA-Z]{36}"), "GitHub personal access token", "CWE-798"),
    (re.compile(r"github_pat_[0-9a-zA-Z_]{20,}"), "GitHub fine-grained PAT", "CWE-798"),
    (re.compile(r"xox[baprs]-[0-9a-zA-Z-]{10,}"), "Slack token", "CWE-798"),
    (re.compile(r"-----BEGIN (RSA|EC|OPENSSH|DSA|PGP) PRIVATE KEY-----"), "Private key", "CWE-321"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "JWT-shaped token", "CWE-522"),
    (
        re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"\s]{12,}['\"]"),
        "Hardcoded credential-shaped assignment",
        "CWE-798",
    ),
]

_SENSITIVE_FILENAMES = {".env", "id_rsa", "id_ecdsa", "id_ed25519"}


def _redact(value: str) -> str:
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}{'•' * 8}{value[-4:]}"


def _iter_text_files(root):
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        for filename in filenames:
            if scanned >= MAX_FILES_SCANNED:
                return
            path = os.path.join(dirpath, filename)
            try:
                if os.path.getsize(path) > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            scanned += 1
            yield path, filename


def _scan_with_builtin_patterns(context: SecurityContext) -> list[SecurityFinding]:
    root = context.workspace.path
    findings: list[SecurityFinding] = []

    for path, filename in _iter_text_files(root):
        rel = os.path.relpath(path, root)
        if filename in _SENSITIVE_FILENAMES:
            findings.append(
                SecurityFinding(
                    id=f"secret-file-{rel}",
                    title=f"Sensitive filename committed: {filename}",
                    category="secrets",
                    severity="high",
                    confidence="medium",
                    impact="A credentials/key file tracked in the repository can leak to anyone with "
                    "repo access, including through history even after later deletion.",
                    remediation="Remove from the repository, add to .gitignore, rotate any credential "
                    "it contained, and purge it from git history if it was ever committed.",
                    scanner=SCANNER_NAME,
                    file=rel,
                    verified="likely",
                )
            )
            continue

        try:
            content = open(path, encoding="utf-8", errors="strict").read()
        except (UnicodeDecodeError, OSError):
            continue

        for pattern, title, cwe in _PATTERNS:
            for match in pattern.finditer(content):
                line = content.count("\n", 0, match.start()) + 1
                findings.append(
                    SecurityFinding(
                        id=f"secret-{rel}-{line}-{title}",
                        title=f"Possible secret: {title}",
                        category="secrets",
                        severity="critical",
                        confidence="medium",
                        impact="If genuine and reachable (public repo, leaked history, shared "
                        "workspace), this credential can be used to impersonate this service.",
                        remediation="Rotate the credential immediately, move it to a secrets manager "
                        "or environment variable never committed to source control, and verify it "
                        "does not also exist elsewhere in git history.",
                        scanner=SCANNER_NAME,
                        file=rel,
                        line=line,
                        evidence=_redact(match.group(0)),
                        cwe=cwe,
                        verified="unverified",
                    )
                )
    return findings


def _run_gitleaks(context: SecurityContext, gitleaks_path: str) -> list[SecurityFinding] | None:
    """Prefers the real gitleaks scanner (also covers git history, which the
    built-in file-content scan does not) when it's actually installed."""
    try:
        result = subprocess.run(
            [gitleaks_path, "detect", "--source", str(context.workspace.path), "--no-git", "-f", "json"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (subprocess.SubprocessError, OSError):
        return None

    import json as _json

    if not result.stdout.strip():
        return []
    try:
        entries = _json.loads(result.stdout)
    except ValueError:
        return None

    findings = []
    for entry in entries:
        secret = entry.get("Secret", "")
        findings.append(
            SecurityFinding(
                id=f"gitleaks-{entry.get('File')}-{entry.get('StartLine')}",
                title=f"Possible secret (gitleaks): {entry.get('RuleID', 'unknown rule')}",
                category="secrets",
                severity="critical",
                confidence="high",
                impact="Same as any exposed credential: usable to impersonate the affected service "
                "if reachable by an attacker.",
                remediation="Rotate the credential, remove it from source, and purge it from git "
                "history if committed.",
                scanner="secrets:gitleaks",
                file=entry.get("File"),
                line=entry.get("StartLine"),
                evidence=_redact(secret) if secret else None,
                verified="likely",
            )
        )
    return findings


def scan(context: SecurityContext, toolchain: dict[str, str | None]) -> ScanOutcome:
    gitleaks_path = toolchain.get("gitleaks")
    if gitleaks_path:
        result = _run_gitleaks(context, gitleaks_path)
        if result is not None:
            return ScanOutcome(scanner=SCANNER_NAME, ran=True, findings=result, detail="gitleaks")

    findings = _scan_with_builtin_patterns(context)
    return ScanOutcome(scanner=SCANNER_NAME, ran=True, findings=findings, detail="built-in patterns")
