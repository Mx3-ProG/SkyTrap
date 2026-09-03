from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from skytrap.autonomy.memory import WorkingMemory


class ExecutionEvidence(BaseModel):
    """Item 7 — STRUCTURED EXECUTION EVIDENCE. Every field here is populated
    exclusively from real tool_result/verification/review/browser_check events
    recorded in `WorkingMemory` — never from the model's own words. The
    boolean *_passed/*_failed fields and requirements_satisfied/unverified are
    what `loop.py`'s final report renders instead of trusting the model's
    "final" message: a claim not backed by one of these fields is not printed
    as fact."""

    files_read: list[str] = Field(default_factory=list)
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)
    files_deleted: list[str] = Field(default_factory=list)
    patches_applied: list[str] = Field(default_factory=list)
    commands_run: list[str] = Field(default_factory=list)
    tests_run: list[dict[str, Any]] = Field(default_factory=list)
    lint_results: list[dict[str, Any]] = Field(default_factory=list)
    typecheck_results: list[dict[str, Any]] = Field(default_factory=list)
    builds: list[dict[str, Any]] = Field(default_factory=list)
    browser_checks: list[dict[str, Any]] = Field(default_factory=list)
    checkpoint: dict[str, Any] | None = None

    # Tri-state: True/False when the stage actually ran, None when it never
    # ran at all — None must never be rendered as either "passed" or "failed".
    tests_passed: bool | None = None
    tests_failed: bool = False
    build_passed: bool | None = None
    build_failed: bool = False
    review_passed: bool | None = None
    browser_verified: bool | None = None
    requirements_satisfied: list[str] = Field(default_factory=list)
    requirements_unverified: list[str] = Field(default_factory=list)

    @classmethod
    def from_memory(
        cls, memory: WorkingMemory, *, success_criteria: list[str] | None = None
    ) -> "ExecutionEvidence":
        evidence = cls(
            files_read=list(memory.files_consulted),
            commands_run=list(memory.commands_executed),
        )
        for event in memory.events:
            data = event.data
            if event.kind == "tool_result" and data.get("success"):
                tool, path = data.get("tool"), data.get("path")
                if path and tool in {"write_file", "patch_file", "delete_file"}:
                    target = (
                        evidence.files_deleted
                        if tool == "delete_file" or data.get("is_delete")
                        else evidence.files_created
                        if data.get("is_new_file")
                        else evidence.files_modified
                    )
                    if path not in target:
                        target.append(path)
                    if tool == "patch_file" and path not in evidence.patches_applied:
                        evidence.patches_applied.append(path)
            elif event.kind == "browser_check":
                evidence.browser_checks.append(data)
            elif event.kind == "checkpoint" and data.get("success"):
                evidence.checkpoint = data
        for verification in memory.verification_results:
            for result in verification.get("results", []):
                stage = result.get("metadata", {}).get("stage")
                target = {
                    "lint": evidence.lint_results,
                    "typecheck": evidence.typecheck_results,
                    "test": evidence.tests_run,
                    "build": evidence.builds,
                }.get(stage)
                if target is not None:
                    target.append(result)

        if evidence.tests_run:
            evidence.tests_passed = all(item.get("success") for item in evidence.tests_run)
            evidence.tests_failed = any(not item.get("success") for item in evidence.tests_run)
        if evidence.builds:
            evidence.build_passed = all(item.get("success") for item in evidence.builds)
            evidence.build_failed = any(not item.get("success") for item in evidence.builds)
        if memory.review_results:
            evidence.review_passed = bool(memory.review_results[-1].get("passed"))
        real_browser_checks = [c for c in evidence.browser_checks if not c.get("skipped")]
        if real_browser_checks:
            evidence.browser_verified = all(c.get("success") for c in real_browser_checks)

        if success_criteria:
            # A requirement only counts as "satisfied" when every real check that
            # actually ran, ran clean — a stage that never ran proves nothing, so
            # any missing/failed stage keeps the criteria in "unverified", never
            # promoted to "satisfied" by omission.
            all_clean = (
                evidence.tests_passed is not False
                and evidence.build_passed is not False
                and evidence.review_passed is not False
                and (evidence.tests_passed or evidence.build_passed or evidence.review_passed)
            )
            if all_clean:
                evidence.requirements_satisfied = list(success_criteria)
            else:
                evidence.requirements_unverified = list(success_criteria)
        return evidence

    def summary(self) -> str:
        return (
            f"{len(self.files_created)} created, {len(self.files_modified)} modified, "
            f"{len(self.files_deleted)} deleted; {len(self.tests_run)} test, "
            f"{len(self.lint_results)} lint, {len(self.typecheck_results)} typecheck, "
            f"{len(self.builds)} build and {len(self.browser_checks)} browser result(s)."
        )

    def render_structured_report(self) -> str:
        """Item 7 — the deterministic replacement for patching an incorrect
        model sentence with a regex correction: build the report directly from
        this evidence, so a claim with no supporting field simply never
        appears at all."""
        lines: list[str] = []
        touched = len(self.files_created) + len(self.files_modified) + len(self.files_deleted)
        if touched:
            parts = []
            if self.files_created:
                parts.append(f"{len(self.files_created)} file(s) created")
            if self.files_modified:
                parts.append(f"{len(self.files_modified)} file(s) modified")
            if self.files_deleted:
                parts.append(f"{len(self.files_deleted)} file(s) deleted")
            lines.append("; ".join(parts) + ".")
        else:
            lines.append("No files were changed.")

        def stage_line(label: str, passed: bool | None, failed: bool) -> None:
            if passed is True:
                lines.append(f"{label} passed.")
            elif failed or passed is False:
                lines.append(f"{label} FAILED.")
            else:
                lines.append(f"{label} not verified.")

        stage_line("Unit tests", self.tests_passed, self.tests_failed)
        stage_line("Build", self.build_passed, self.build_failed)
        if self.review_passed is not None:
            lines.append("Independent review passed." if self.review_passed else "Independent review FAILED.")
        if self.browser_verified is not None:
            lines.append("Browser/end-to-end behavior verified." if self.browser_verified else "Browser/end-to-end verification FAILED.")
        else:
            lines.append("End-to-end browser behavior not verified.")

        if self.requirements_unverified:
            lines.append(
                "Not independently confirmed: " + "; ".join(self.requirements_unverified[:5])
            )
        return "\n".join(lines)
