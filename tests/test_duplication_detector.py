"""Item 4 — SEMANTIC DUPLICATION DETECTOR.

The last pass fixed re-"creating" a path that already exists under the exact
same name (index.html). This proves the remaining gap is closed too: a plan
proposing a NEW path for a capability that already exists under a DIFFERENT
name — the spec's own example (proposing src/services/auth.ts when
src/auth/authService.ts already implements an AuthService).
"""

from pathlib import Path

from skytrap.core.context import detect_workspace
from skytrap.intelligence.duplication import ExistingCapabilityDetector
from skytrap.intelligence.existence import ExistenceStatus
from skytrap.intelligence.snapshot import build_repository_snapshot
from skytrap.intelligence.symbols import SymbolIndex


def _init_repo(root: Path) -> None:
    (root / "src" / "auth").mkdir(parents=True)
    (root / "src" / "auth" / "authService.ts").write_text(
        "export class AuthService {\n"
        "  login(user: string, password: string) {\n"
        "    return true;\n"
        "  }\n"
        "}\n"
    )
    (root / "src" / "index.ts").write_text("export {};\n")


def test_detects_duplicate_capability_under_a_different_proposed_filename(tmp_path):
    _init_repo(tmp_path)
    ws = detect_workspace(tmp_path)
    snapshot = build_repository_snapshot(ws)
    index = SymbolIndex().build(ws)

    detector = ExistingCapabilityDetector(symbol_index=index)
    evidence = detector.check(
        ws,
        snapshot,
        description="Add an authentication service",
        proposed_path="src/services/auth.ts",
    )

    assert evidence.status in {ExistenceStatus.EXISTS, ExistenceStatus.PARTIAL}
    assert "src/auth/authService.ts" in evidence.matched_files or "AuthService" in evidence.matched_symbols


def test_genuinely_new_capability_is_not_flagged_as_duplicate(tmp_path):
    _init_repo(tmp_path)
    ws = detect_workspace(tmp_path)
    snapshot = build_repository_snapshot(ws)
    index = SymbolIndex().build(ws)

    detector = ExistingCapabilityDetector(symbol_index=index)
    evidence = detector.check(
        ws,
        snapshot,
        description="Add a PDF invoice export feature",
        proposed_path="src/billing/invoiceExporter.ts",
    )

    assert evidence.status == ExistenceStatus.MISSING


def test_planner_downgrades_create_to_reuse_for_a_same_capability_different_name(tmp_path):
    from skytrap.autonomy.intent import HumanIntentEngine
    from skytrap.autonomy.planning import FileActionType, PlanFileAction, TaskPlan
    from skytrap.autonomy.planning import _reconcile_duplication

    _init_repo(tmp_path)
    ws = detect_workspace(tmp_path)
    snapshot = build_repository_snapshot(ws)
    index = SymbolIndex().build(ws)
    intent = HumanIntentEngine().normalize("Implement authentication service", workspace=ws)

    naive_plan = TaskPlan(
        summary="Add an auth service",
        steps=[],
        files=["src/services/auth.ts"],
        file_actions=[
            PlanFileAction(
                path="src/services/auth.ts",
                action=FileActionType.CREATE,
                justification="need an authentication service",
            )
        ],
    )

    reconciled = _reconcile_duplication(ws, snapshot, naive_plan, intent, index)

    assert reconciled.file_actions[0].action in {FileActionType.REUSE, FileActionType.MODIFY}
    assert "authService" in reconciled.file_actions[0].justification or "AuthService" in reconciled.file_actions[0].justification
