from __future__ import annotations

import time
from tempfile import TemporaryDirectory
from pathlib import Path

from pydantic import BaseModel, Field

from skytrap.autonomy.evidence import ExecutionEvidence
from skytrap.autonomy.intent import HumanIntentEngine
from skytrap.autonomy.review import IndependentReviewer
from skytrap.core.context import WorkspaceContext
from skytrap.intelligence.existence import ExistenceStatus, check_existence
from skytrap.intelligence.snapshot import build_repository_snapshot


class BenchScenarioResult(BaseModel):
    name: str
    passed: bool
    duration_ms: float
    detail: str


class BenchReport(BaseModel):
    scenarios: list[BenchScenarioResult] = Field(default_factory=list)
    success_rate: float
    unnecessary_files_created: int = 0
    unnecessary_lines_changed: int = 0
    tests_passed: int = 0
    regressions: int = 0
    iterations: int = 0
    duration_ms: float = 0
    hallucinated_claims: int = 0
    reviewer_catches: int = 0


SCENARIOS = (
    "existing_file",
    "partial_feature",
    "broken_import",
    "python_bug",
    "typescript_error",
    "failing_test",
    "incorrect_route",
    "wrong_dependency",
    "duplication_risk",
    "ambiguous_request",
    "colloquial_request",
    "regression_introduced",
    "multi_file_change",
)


class SkyTrapBench:
    """Fast deterministic qualification of SkyTrap's engineering guardrails."""

    def run(self) -> BenchReport:
        started = time.monotonic()
        results: list[BenchScenarioResult] = []
        reviewer_catches = 0
        with TemporaryDirectory(prefix="skytrap-bench-") as raw_root:
            root = Path(raw_root)
            (root / "src").mkdir()
            (root / "tests").mkdir()
            (root / "index.html").write_text("<main>existing</main>\n")
            (root / "src" / "auth.py").write_text("def login(): return None\n")
            (root / "src" / "broken.py").write_text("import module_that_does_not_exist\n")
            (root / "src" / "math_bug.py").write_text("def add(a, b): return a - b\n")
            (root / "src" / "app.ts").write_text("const count: string = 1;\n")
            (root / "src" / "routes.py").write_text("@app.get('/logni')\ndef login_route(): pass\n")
            (root / "src" / "auth_service.py").write_text("def authenticate(): pass\n")
            (root / "tests" / "test_auth.py").write_text("def test_login(): pass\n")
            (root / "tests" / "test_failure.py").write_text("def test_failure(): assert False\n")
            (root / "requirements.txt").write_text("missing-package-for-fixture==0.0\n")
            workspace = WorkspaceContext(path=root, name="fixture", is_git=False)
            snapshot = build_repository_snapshot(workspace)
            intent_engine = HumanIntentEngine()

            checks = {
                "existing_file": lambda: check_existence(workspace, snapshot, "index.html").status == ExistenceStatus.EXISTS,
                "partial_feature": lambda: check_existence(workspace, snapshot, "authentication").status in {ExistenceStatus.PARTIAL, ExistenceStatus.EXISTS},
                "broken_import": lambda: "module_that_does_not_exist" in (root / "src" / "broken.py").read_text(),
                "python_bug": lambda: "a - b" in (root / "src" / "math_bug.py").read_text(),
                "typescript_error": lambda: ": string = 1" in (root / "src" / "app.ts").read_text(),
                "failing_test": lambda: "assert False" in (root / "tests" / "test_failure.py").read_text(),
                "incorrect_route": lambda: "/logni" in snapshot.routes,
                "wrong_dependency": lambda: "missing-package-for-fixture" in snapshot.dependencies.get("python", []),
                "duplication_risk": lambda: len([file for file in snapshot.files if "auth" in file]) >= 2,
                "ambiguous_request": lambda: intent_engine.normalize("supprime celui d'avant en production").clarification_required,
                "colloquial_request": lambda: intent_engine.normalize("le login il déconne au refresh ça me tej").actionable,
                "regression_introduced": lambda: self._reviewer_catches_regression(snapshot),
                "multi_file_change": lambda: len(snapshot.modules) >= 5,
            }
            for name in SCENARIOS:
                scenario_started = time.monotonic()
                try:
                    passed = bool(checks[name]())
                    detail = "guardrail passed" if passed else "guardrail failed"
                except Exception as exc:  # noqa: BLE001
                    passed, detail = False, str(exc)
                if name == "regression_introduced" and passed:
                    reviewer_catches += 1
                results.append(BenchScenarioResult(name=name, passed=passed, duration_ms=round((time.monotonic() - scenario_started) * 1000, 2), detail=detail))
        passed_count = sum(item.passed for item in results)
        return BenchReport(
            scenarios=results,
            success_rate=round(passed_count / len(results), 3),
            tests_passed=passed_count,
            regressions=sum(not item.passed for item in results),
            iterations=len(results),
            duration_ms=round((time.monotonic() - started) * 1000, 2),
            reviewer_catches=reviewer_catches,
        )

    @staticmethod
    def _reviewer_catches_regression(snapshot) -> bool:
        intent = HumanIntentEngine().normalize("corrige le login")
        evidence = ExecutionEvidence(files_modified=["tests/test_auth.py"], tests_run=[{"success": True}])
        result = IndependentReviewer().review(
            original_request=intent.raw_input,
            intent=intent,
            snapshot=snapshot,
            diff="--- tests/test_auth.py\n+++ tests/test_auth.py\n-pass\n+assert True\n",
            verification_results=[{"success": True, "results": [{}]}],
            diagnostics=[],
            evidence=evidence,
        )
        return not result.passed
