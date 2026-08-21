from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["info", "low", "medium", "high", "critical"]
Confidence = Literal["low", "medium", "high"]
VerificationStatus = Literal["unverified", "likely", "confirmed", "false_positive", "fixed"]


@dataclass
class SecurityFinding:
    """One thing a scanner observed, never presented as more certain than it is —
    severity (how bad IF real) and confidence (how sure we are it's real) are always
    tracked separately; a CRITICAL/LOW-confidence finding is not the same claim as a
    MEDIUM/HIGH-confidence one, and both must be shown as what they are."""

    id: str
    title: str
    category: str
    severity: Severity
    confidence: Confidence
    impact: str
    remediation: str
    scanner: str
    file: str | None = None
    line: int | None = None
    asset: str | None = None
    evidence: str | None = None
    cwe: str | None = None
    cve: str | None = None
    verified: VerificationStatus = "unverified"


@dataclass
class ScanOutcome:
    """What a single scanner actually did — used to render honest progress/summary
    lines ("✓ dependency scan completed" only appears if this says so) instead of a
    fabricated "done" for a step that was skipped or failed to run."""

    scanner: str
    ran: bool
    findings: list[SecurityFinding] = field(default_factory=list)
    skipped_reason: str | None = None
    detail: str | None = None
