from dataclasses import dataclass, field

from skytrap.security.findings import ScanOutcome, Severity, SecurityFinding

_SEVERITY_ORDER: dict[Severity, int] = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}


@dataclass
class SecurityReport:
    target: str
    outcomes: list[ScanOutcome] = field(default_factory=list)

    @property
    def findings(self) -> list[SecurityFinding]:
        all_findings = [f for outcome in self.outcomes for f in outcome.findings]
        return sorted(all_findings, key=lambda f: _SEVERITY_ORDER[f.severity], reverse=True)

    def counts_by_severity(self) -> dict[Severity, int]:
        counts: dict[Severity, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        for finding in self.findings:
            counts[finding.severity] += 1
        return counts

    def worst_severity(self) -> Severity | None:
        for severity in ("critical", "high", "medium", "low", "info"):
            if self.counts_by_severity()[severity] > 0:
                return severity
        return None

    def fails_threshold(self, threshold: Severity) -> bool:
        """For `--ci`: true if any finding meets or exceeds the threshold severity."""
        worst = self.worst_severity()
        if worst is None:
            return False
        return _SEVERITY_ORDER[worst] >= _SEVERITY_ORDER[threshold]
