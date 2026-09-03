from __future__ import annotations

import json
import re
from enum import StrEnum

from pydantic import BaseModel, Field, ValidationError

from skytrap.autonomy.evidence import ExecutionEvidence
from skytrap.autonomy.intent import NormalizedIntent
from skytrap.intelligence.snapshot import RepositorySnapshot
from skytrap.models.base import ModelProvider


class ReviewSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    SERIOUS = "serious"


class ReviewFinding(BaseModel):
    severity: ReviewSeverity
    category: str
    detail: str


class ReviewResult(BaseModel):
    passed: bool
    findings: list[ReviewFinding] = Field(default_factory=list)
    reviewer: str = "deterministic-independent-reviewer"


class IndependentReviewer:
    """Reviews only task inputs and external evidence, never coder conversation."""

    def __init__(self, model: ModelProvider | None = None) -> None:
        self.model = model

    def review(
        self,
        *,
        original_request: str,
        intent: NormalizedIntent,
        snapshot: RepositorySnapshot,
        diff: str,
        verification_results: list[dict],
        diagnostics: list[str],
        evidence: ExecutionEvidence,
    ) -> ReviewResult:
        findings = self._deterministic_findings(intent, diff, verification_results, evidence)
        if self.model is not None:
            findings.extend(
                self._model_findings(
                    original_request, intent, snapshot, diff, verification_results, diagnostics
                )
            )
        serious = any(item.severity == ReviewSeverity.SERIOUS for item in findings)
        return ReviewResult(
            passed=not serious,
            findings=findings,
            reviewer=self.model.name if self.model else "deterministic-independent-reviewer",
        )

    @staticmethod
    def _deterministic_findings(
        intent: NormalizedIntent,
        diff: str,
        verification_results: list[dict],
        evidence: ExecutionEvidence,
    ) -> list[ReviewFinding]:
        findings: list[ReviewFinding] = []
        if not any(item.get("success") and item.get("results") for item in verification_results):
            findings.append(ReviewFinding(severity=ReviewSeverity.SERIOUS, category="verification", detail="No successful real verification result."))
        changed = [*evidence.files_created, *evidence.files_modified, *evidence.files_deleted]
        test_changes = [path for path in changed if re.search(r"(^|/)(tests?|__tests__)/|test_|\.test\.", path)]
        source_changes = [path for path in changed if path not in test_changes]
        if test_changes and not source_changes and not re.search(r"\btest", intent.interpreted_goal, re.I):
            findings.append(ReviewFinding(severity=ReviewSeverity.SERIOUS, category="test_integrity", detail="Only tests changed for a non-test objective; this may manufacture a passing result."))
        additions = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
        deletions = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
        if additions + deletions > 800:
            findings.append(ReviewFinding(severity=ReviewSeverity.WARNING, category="change_scope", detail=f"Large change set: {additions + deletions} changed lines; verify a rewrite was necessary."))
        return findings

    def _model_findings(self, request, intent, snapshot, diff, verification, diagnostics):
        prompt = {
            "original_request": request,
            "normalized_intent": intent.model_dump(mode="json"),
            "architecture": snapshot.evidence_lines(),
            "diff": diff[-20000:],
            "verification": verification,
            "diagnostics": diagnostics[-20:],
        }
        system = (
            "You are an independent code reviewer. You have no coder reasoning. Check objective, "
            "duplication, architecture, regressions, security, dead code, test tampering and unnecessary "
            "changes. Return JSON {\"findings\":[{\"severity\":\"info|warning|serious\","
            "\"category\":\"...\",\"detail\":\"...\"}]} only."
        )
        try:
            raw = self.model.chat([{"role": "system", "content": system}, {"role": "user", "content": json.dumps(prompt)}])
            data = json.loads(raw)
            return [ReviewFinding.model_validate(item) for item in data.get("findings", [])]
        except Exception:  # noqa: BLE001 - invalid/unavailable reviewer degrades to deterministic review
            return [ReviewFinding(severity=ReviewSeverity.WARNING, category="reviewer", detail="Model reviewer returned an invalid structured response; deterministic review still ran.")]
