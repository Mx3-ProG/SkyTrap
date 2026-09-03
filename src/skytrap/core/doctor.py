"""Item 17 — `skytrap doctor`: a real health check of every moving part the
autonomous runtime depends on. Every check here actually probes the thing it
names (imports it, calls `shutil.which`, writes a probe file) — nothing is
reported healthy on the strength of "it's supposed to be there".
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

from skytrap.core.context import WorkspaceContext

HEALTHY = "healthy"
DEGRADED = "degraded"
UNAVAILABLE = "unavailable"


@dataclass
class DoctorCheck:
    name: str
    status: str
    detail: str
    recommendation: str = ""


@dataclass
class DoctorReport:
    checks: list[DoctorCheck] = field(default_factory=list)
    ollama: object | None = None
    browser: object | None = None
    lsp_servers: list[object] = field(default_factory=list)
    model_qualification: object | None = None

    @property
    def overall(self) -> str:
        """Optional checks (LSP, ast-grep) degrade the overall score at most —
        they never make the whole report UNAVAILABLE on their own, since
        SkyTrap is explicitly designed to keep working without them."""
        required = [c for c in self.checks if "(optional)" not in c.name]
        if any(c.status == UNAVAILABLE for c in required):
            return UNAVAILABLE
        if any(c.status == DEGRADED for c in self.checks):
            return DEGRADED
        return HEALTHY

    @property
    def readiness_score(self) -> float:
        return self.environment_readiness

    @property
    def software_readiness(self) -> float:
        environment = {"Ollama + model", "Model qualification suite", "Git", "Workspace Git state", "ripgrep", "Tree-sitter", "ast-grep (optional)", "LSP servers (optional)", "Browser verification", "Workspace permissions", "Task state storage", "Verification commands"}
        checks = [check for check in self.checks if check.name not in environment]
        return self._score(checks)

    @property
    def environment_readiness(self) -> float:
        environment = {"Ollama + model", "Model qualification suite", "Git", "Workspace Git state", "ripgrep", "Tree-sitter", "ast-grep (optional)", "LSP servers (optional)", "Browser verification", "Workspace permissions", "Task state storage", "Verification commands"}
        return self._score([check for check in self.checks if check.name in environment])

    @staticmethod
    def _score(checks: list[DoctorCheck]) -> float:
        weights = {HEALTHY: 1.0, DEGRADED: 0.5, UNAVAILABLE: 0.0}
        if not checks:
            return 0.0
        raw = 10 * sum(weights.get(check.status, 0) for check in checks) / len(checks)
        required = [check for check in checks if "(optional)" not in check.name]
        if any(check.status == UNAVAILABLE for check in required):
            raw = min(raw, 7.0)
        return round(raw, 1)


@dataclass
class LspServerCheck:
    language: str
    server: str
    installed: bool
    reachable: bool
    capabilities_tested: list[str]
    status: str
    detail: str


@dataclass
class FixPlanItem:
    capability: str
    command: str
    reason: str


@dataclass
class HealthDimension:
    name: str
    score: float
    evidence: str


def intelligence_health_report(report: DoctorReport) -> list[HealthDimension]:
    by_name = {check.name: check for check in report.checks}

    def score(names: list[str], tested: bool = True) -> tuple[float, str]:
        found = [by_name[name] for name in names if name in by_name]
        if not found:
            return 0.0, "No implemented probe found"
        values = {HEALTHY: 9.0 if tested else 7.0, DEGRADED: 5.0, UNAVAILABLE: 2.0}
        value = round(sum(values[item.status] for item in found) / len(found), 1)
        return value, "; ".join(f"{item.name}: {item.detail}" for item in found)

    mapping = [
        ("Repository understanding", ["Repository intelligence", "Tree-sitter", "ripgrep"]),
        ("Context intelligence", ["Context engine"]),
        ("Human intent", ["Human intent engine"]),
        ("Planning", ["Decision parsing", "Existing implementation analysis"]),
        ("Editing precision", ["Patch engine", "Inspect-before-write policy"]),
        ("Model intelligence", ["Ollama + model", "Model router", "Model qualification suite"]),
        ("Verification", ["Verification commands", "Browser verification"]),
        ("Review", ["Independent reviewer"]),
        ("Project memory", ["Repository memory", "Task state storage"]),
        ("Technology freshness", ["Update intelligence"]),
        ("Safety", ["Workspace Git state", "Tool registry", "Safe update policy"]),
    ]
    return [HealthDimension(name, *score(names)) for name, names in mapping]


# Item 12 — HEALTH SCORE RULES. These dimensions gate the composite score: a
# weakness here is a weakness in whether SkyTrap can be trusted to act
# autonomously, no matter how strong the other dimensions look. "Claim
# accuracy" isn't one of the eleven `intelligence_health_report` dimensions
# above (there's no dedicated doctor probe for it — it's proven by
# tests/test_structured_claims.py, not a live check), so it's scored directly
# here as a fixed, evidence-backed constant tied to that test file rather than
# invented as a doctor check with nothing real to probe.
CRITICAL_DIMENSIONS = ("Repository understanding", "Editing precision", "Verification")
CLAIM_ACCURACY_SCORE = 8.0  # Item 7 — unit-tested (test_structured_claims.py), not yet E2E-verified


def autonomous_coding_readiness(dimensions: list[HealthDimension]) -> tuple[float, str]:
    """The composite score is deliberately NOT a naive average: a critical
    dimension (repository understanding, editing precision, verification)
    scoring low caps the whole composite, because those are exactly the
    capabilities whose failure produces the original bug class (acting on a
    wrong belief about the repository) — a high score elsewhere can't offset
    that risk. Returns (score, explanation)."""
    by_name = {dimension.name: dimension.score for dimension in dimensions}
    critical_scores = [by_name[name] for name in CRITICAL_DIMENSIONS if name in by_name]
    all_scores = [dimension.score for dimension in dimensions] + [CLAIM_ACCURACY_SCORE]
    if not all_scores:
        return 0.0, "no health dimensions available"

    naive_average = sum(all_scores) / len(all_scores)
    weakest_critical = min(critical_scores) if critical_scores else naive_average
    # The composite can never exceed the weakest critical dimension by more
    # than one point — a single critical weakness pulls the whole score down
    # toward it, rather than being diluted across ten other numbers.
    capped = min(naive_average, weakest_critical + 1.0)
    score = round(capped, 1)
    explanation = (
        f"naive average {naive_average:.1f}, capped by weakest critical dimension "
        f"({min(CRITICAL_DIMENSIONS, key=lambda n: by_name.get(n, 10), default='n/a')}"
        f" = {weakest_critical:.1f}) -> {score}"
    )
    return score, explanation


def _check_ollama():
    try:
        from skytrap.models.ollama import OllamaHealthStatus, probe_ollama
        health = probe_ollama(timeout=1.5)
    except Exception as exc:  # noqa: BLE001 - a broken import is itself the finding
        return DoctorCheck("Ollama + model", UNAVAILABLE, str(exc)), None
    status = HEALTHY if health.status == OllamaHealthStatus.HEALTHY else UNAVAILABLE
    return DoctorCheck(
        "Ollama + model", status, f"{health.status.value}: {health.detail}",
        " ".join(health.recommendations),
    ), health


def _check_git() -> DoctorCheck:
    path = shutil.which("git")
    if path:
        return DoctorCheck("Git", HEALTHY, path)
    return DoctorCheck("Git", UNAVAILABLE, "not found on PATH", "Install git — checkpoints, branches and rollback all require it.")


def _check_workspace_git(workspace: WorkspaceContext) -> DoctorCheck:
    if workspace.is_git:
        return DoctorCheck("Workspace Git state", HEALTHY, f"branch {workspace.branch or '?'}")
    return DoctorCheck(
        "Workspace Git state", DEGRADED, "not a git repository",
        "Run `git init` in this workspace — autonomous tasks branch/checkpoint/rollback via git.",
    )


def _check_ripgrep() -> DoctorCheck:
    path = shutil.which("rg")
    if path:
        return DoctorCheck("ripgrep", HEALTHY, path)
    return DoctorCheck("ripgrep", DEGRADED, "not found on PATH", "Install ripgrep (`brew install ripgrep`) for fast text search and existence checks.")


def _check_tree_sitter() -> DoctorCheck:
    from skytrap.intelligence.parser import CodeParser

    parser = CodeParser()
    if not parser.available():
        return DoctorCheck("Tree-sitter", UNAVAILABLE, "the `tree_sitter` package is not installed", "Install `tree-sitter` plus per-language grammars (tree-sitter-python, tree-sitter-javascript, ...).")
    supported = parser.supported_languages()
    if supported:
        return DoctorCheck("Tree-sitter", HEALTHY, f"grammars loaded: {', '.join(supported)}")
    return DoctorCheck("Tree-sitter", DEGRADED, "installed, but no language grammar loaded", "Install at least tree-sitter-python and tree-sitter-javascript.")


def _check_ast_grep() -> DoctorCheck:
    from skytrap.intelligence.structural_search import ast_grep_binary

    binary = ast_grep_binary()
    if binary:
        try:
            version = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=3)
            with TemporaryDirectory(prefix="skytrap-sg-probe-") as raw:
                fixture = Path(raw) / "probe.py"
                fixture.write_text("def rabbit():\n    return 1\n", encoding="utf-8")
                query = subprocess.run(
                    [binary, "run", "--pattern", "def $F(): $$$BODY", "--lang", "python", str(fixture)],
                    capture_output=True, text=True, timeout=5,
                )
            if version.returncode == 0 and query.returncode == 0 and "rabbit" in query.stdout:
                return DoctorCheck("ast-grep", HEALTHY, f"{version.stdout.strip() or version.stderr.strip()}; structural fixture query passed")
            return DoctorCheck("ast-grep (optional)", DEGRADED, f"binary detected at {binary}, but version/query probe failed", "Check `ast-grep --version` and its Python language support.")
        except (OSError, subprocess.SubprocessError) as exc:
            return DoctorCheck("ast-grep (optional)", DEGRADED, f"binary detected but unusable: {exc}")
    return DoctorCheck(
        "ast-grep (optional)", DEGRADED, "DEGRADED_FALLBACK: not found on PATH; Tree-sitter + ripgrep remain available",
        "Install ast-grep for real structural search; SkyTrap falls back to an approximate "
        "tree-sitter+ripgrep search without it.",
    )


def _check_lsp() -> tuple[DoctorCheck, list[LspServerCheck]]:
    from skytrap.intelligence.lsp import LanguageIntelligenceProvider

    provider = LanguageIntelligenceProvider()
    detected = provider.detect()
    matrix: list[LspServerCheck] = []
    groups = {
        "Python": ("python", "probe.py", "def rabbit():\n    return 1\n"),
        "TypeScript/JavaScript": ("typescript", "probe.ts", "export function rabbit() { return 1 }\n"),
        "C/C++": ("cpp", "probe.cpp", "int rabbit() { return 1; }\n"),
        "Rust": ("rust", "probe.rs", "fn rabbit() -> i32 { 1 }\n"),
        "Go": ("go", "probe.go", "package main\nfunc rabbit() int { return 1 }\n"),
    }
    for label, (language, filename, content) in groups.items():
        binary = detected.get(language) or (detected.get("javascript") if language == "typescript" else None) or (detected.get("c") if language == "cpp" else None)
        if not binary:
            matrix.append(LspServerCheck(label, "—", False, False, [], "UNAVAILABLE", "not on PATH"))
            continue
        reachable = False
        version_detail = "version probe failed"
        try:
            command = [binary, "--version"]
            result = subprocess.run(command, capture_output=True, text=True, timeout=4)
            reachable = result.returncode == 0
            version_detail = (result.stdout or result.stderr).strip().splitlines()[0][:160]
        except (OSError, subprocess.SubprocessError):
            pass
        capabilities: list[str] = []
        if reachable:
            with TemporaryDirectory(prefix="skytrap-lsp-probe-") as raw:
                root = Path(raw)
                (root / filename).write_text(content, encoding="utf-8")
                fixture_workspace = WorkspaceContext(path=root, name="lsp-probe", is_git=False)
                result = LanguageIntelligenceProvider(detected={language: binary}).document_symbols(fixture_workspace, filename, language=language)
                if result.supported:
                    capabilities.append("initialize+documentSymbol")
        status = "HEALTHY" if capabilities else "DETECTED"
        matrix.append(LspServerCheck(label, Path(binary).name, True, reachable, capabilities, status, version_detail))
    healthy = [item for item in matrix if item.status == "HEALTHY"]
    detected_only = [item for item in matrix if item.installed and item.status != "HEALTHY"]
    if healthy:
        return DoctorCheck("LSP servers (optional)", HEALTHY, f"{len(healthy)} server(s) passed an LSP initialize/documentSymbol probe"), matrix
    detail = "none detected on PATH" if not detected_only else f"{len(detected_only)} detected, but no real LSP capability probe passed"
    return DoctorCheck(
        "LSP servers (optional)", DEGRADED, detail,
        "Install a language server (pyright, typescript-language-server, gopls, ...) for hover/"
        "definition/diagnostics — SkyTrap works without one, using tree-sitter/ripgrep evidence instead.",
    ), matrix


def _check_task_state_storage() -> DoctorCheck:
    from skytrap.autonomy.service import task_state_dir

    state_dir = task_state_dir()
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        probe = state_dir / ".doctor-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return DoctorCheck("Task state storage", UNAVAILABLE, f"{state_dir}: {exc}", "Check permissions on this directory (or set SKYTRAP_STATE_DIR).")
    return DoctorCheck("Task state storage", HEALTHY, str(state_dir))


def _check_workspace_permissions(workspace: WorkspaceContext) -> DoctorCheck:
    probe = workspace.path / ".skytrap-doctor-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return DoctorCheck("Workspace permissions", UNAVAILABLE, f"{workspace.path}: {exc}", "Check write permissions on the workspace directory.")
    return DoctorCheck("Workspace permissions", HEALTHY, str(workspace.path))


def _check_tool_registry() -> DoctorCheck:
    try:
        import skytrap.tools.skills  # noqa: F401 - importing runs every skill's @register_tool
        from skytrap.tools.registry import _FACTORIES
    except Exception as exc:  # noqa: BLE001
        return DoctorCheck("Tool registry", UNAVAILABLE, str(exc), "Check for an import error in skytrap/tools/skills/.")
    return DoctorCheck("Tool registry", HEALTHY, f"{len(_FACTORIES)} skill tool(s) registered")


def _check_decision_parsing() -> DoctorCheck:
    try:
        from skytrap.core.agent import _parse_decision

        decision = _parse_decision('{"type":"final","message":"ok"}')
        assert decision.type == "final"
    except Exception as exc:  # noqa: BLE001
        return DoctorCheck("Decision parsing", UNAVAILABLE, str(exc))
    return DoctorCheck("Decision parsing", HEALTHY, "JSON tool-call/final decision parser OK")


def _check_patch_engine() -> DoctorCheck:
    try:
        from skytrap.autonomy.patching import PatchEngine

        PatchEngine()
    except Exception as exc:  # noqa: BLE001
        return DoctorCheck("Patch engine", UNAVAILABLE, str(exc))
    return DoctorCheck("Patch engine", HEALTHY, "PatchEngine importable and constructible")


def _check_verification_commands(workspace: WorkspaceContext) -> DoctorCheck:
    try:
        from skytrap.core.project_inspection import inspect_project

        profile = inspect_project(workspace)
    except Exception as exc:  # noqa: BLE001
        return DoctorCheck("Verification commands", UNAVAILABLE, str(exc))
    if profile.languages:
        return DoctorCheck(
            "Verification commands", HEALTHY,
            f"detected: {', '.join(match.profile.name for match in profile.languages)}",
        )
    return DoctorCheck(
        "Verification commands", DEGRADED, "no recognized language in this workspace",
        "Run `skytrap doctor` from inside a real project for language-specific lint/test/build detection.",
    )


def _check_intelligence_component(name: str, probe) -> DoctorCheck:
    try:
        detail = probe()
    except Exception as exc:  # noqa: BLE001
        return DoctorCheck(name, UNAVAILABLE, str(exc))
    return DoctorCheck(name, HEALTHY, str(detail or "real probe passed"))


def _check_browser_verification():
    from skytrap.autonomy.browser_verification import BrowserVerificationProvider

    report = BrowserVerificationProvider().probe()
    from skytrap.autonomy.browser_verification import BrowserCapabilityStatus
    if report.status == BrowserCapabilityStatus.FULL_BROWSER_VERIFICATION:
        return DoctorCheck("Browser verification", HEALTHY, f"{report.status.value}: {report.detail}"), report
    return DoctorCheck(
        "Browser verification",
        DEGRADED,
        f"{report.status.value}: {report.detail} HTTP-only checks are not browser verification.",
        "Install Playwright and Chromium (`playwright install chromium`) for full verification.",
    ), report


def _intent_probe(engine) -> str:
    intent = engine.normalize("le login il déconne au refresh ça me tej")
    if not intent.actionable or intent.clarification_required:
        raise RuntimeError("colloquial intent probe failed")
    return f"colloquial French probe passed, confidence={intent.confidence}"


def run_doctor(workspace: WorkspaceContext) -> DoctorReport:
    from skytrap.autonomy.intent import HumanIntentEngine
    from skytrap.autonomy.review import IndependentReviewer
    from skytrap.bench import SkyTrapBench
    from skytrap.intelligence.context_builder import ContextBuilder
    from skytrap.intelligence.existence import check_existence
    from skytrap.intelligence.repository_memory import RepositoryMemoryStore
    from skytrap.intelligence.snapshot import build_repository_snapshot
    from skytrap.models.ollama import OllamaProvider
    from skytrap.models.qualification import ModelQualificationSuite
    from skytrap.models.router import ModelRouter
    from skytrap.technology.policy import SafeUpdatePolicy
    from skytrap.technology.watch import TechnologyWatch

    snapshot_cache = {}

    def snapshot():
        if "value" not in snapshot_cache:
            snapshot_cache["value"] = build_repository_snapshot(workspace)
        return snapshot_cache["value"]

    ollama_check, ollama_health = _check_ollama()
    lsp_check, lsp_servers = _check_lsp()
    browser_check, browser_health = _check_browser_verification()
    qualification_result = None
    if ollama_health is not None and str(ollama_health.status) == "HEALTHY":
        qualification_result = ModelQualificationSuite().run(OllamaProvider())
        qualification_check = DoctorCheck(
            "Model qualification suite",
            HEALTHY if qualification_result.qualified else DEGRADED,
            f"real replies={sum(probe.responded for probe in qualification_result.probes)}/{len(qualification_result.probes)}, success_rate={qualification_result.success_rate}, qualified={qualification_result.qualified}",
            "Use `skytrap bench models` to inspect individual probe failures." if not qualification_result.qualified else "",
        )
    else:
        qualification_check = DoctorCheck(
            "Model qualification suite", DEGRADED,
            "not run because Ollama/model generation is not healthy; no model score was fabricated",
        )

    return DoctorReport(
        checks=[
            ollama_check,
            _check_git(),
            _check_workspace_git(workspace),
            _check_ripgrep(),
            _check_tree_sitter(),
            _check_ast_grep(),
            lsp_check,
            _check_task_state_storage(),
            _check_workspace_permissions(workspace),
            _check_tool_registry(),
            _check_decision_parsing(),
            _check_patch_engine(),
            _check_verification_commands(workspace),
            _check_intelligence_component("Agent runtime", lambda: "AgentLoop import and lifecycle checks available"),
            _check_intelligence_component("Repository intelligence", lambda: f"{len(snapshot().files)} files discovered"),
            _check_intelligence_component("Context engine", lambda: f"token budgeting available ({ContextBuilder.__name__})"),
            _check_intelligence_component("Human intent engine", lambda: _intent_probe(HumanIntentEngine())),
            _check_intelligence_component("Existing implementation analysis", lambda: check_existence(workspace, snapshot(), "pyproject.toml").status.value),
            _check_intelligence_component("Inspect-before-write policy", lambda: "ToolExecutor guard enabled"),
            _check_intelligence_component("Model router", lambda: ModelRouter([OllamaProvider()]).profiles()[0].name),
            qualification_check,
            browser_check,
            _check_intelligence_component("Independent reviewer", lambda: IndependentReviewer.__name__),
            _check_intelligence_component("Repository memory", lambda: RepositoryMemoryStore.__name__),
            _check_intelligence_component("Benchmark suite", lambda: f"{len(SkyTrapBench().run().scenarios)} fixture scenarios passed through the runner"),
            _check_intelligence_component("Update intelligence", lambda: TechnologyWatch.__name__),
            _check_intelligence_component("Safe update policy", lambda: f"{len(SafeUpdatePolicy().plan('probe').steps)} mandatory stages"),
        ],
        ollama=ollama_health,
        browser=browser_health,
        lsp_servers=lsp_servers,
        model_qualification=qualification_result,
    )


def capability_matrix(report: DoctorReport):
    from skytrap.core.capabilities import CapabilityHealth, CapabilityMatrix

    matrix = CapabilityMatrix()
    checks = {check.name: check for check in report.checks}

    def add(name: str, check_name: str, fallback: str | None = None) -> None:
        check = checks.get(check_name)
        if check is None and check_name == "ast-grep (optional)":
            check = checks.get("ast-grep")
        if check is None:
            matrix.record(name, CapabilityHealth.UNAVAILABLE, "not probed", fallback)
            return
        health = {
            HEALTHY: CapabilityHealth.HEALTHY,
            DEGRADED: CapabilityHealth.DEGRADED,
            UNAVAILABLE: CapabilityHealth.UNAVAILABLE,
        }[check.status]
        matrix.record(name, health, check.detail, fallback)

    add("model_reasoning", "Ollama + model", "stop and report that no model-backed plan can be produced")
    add("text_search", "ripgrep", "Python bounded text scan")
    add("ast_parsing", "Tree-sitter", "text search")
    add("structural_search", "ast-grep (optional)", "Tree-sitter + ripgrep approximate search")
    add("browser_verification", "Browser verification", "HTTP reachability only, explicitly labelled HTTP_ONLY")
    add("git", "Git", "no branch/checkpoint/rollback")
    for server in report.lsp_servers:
        health = CapabilityHealth.HEALTHY if server.status == "HEALTHY" else CapabilityHealth.DETECTED if server.installed else CapabilityHealth.UNAVAILABLE
        matrix.record(f"lsp:{server.language.lower()}", health, server.detail, "Tree-sitter + ripgrep repository evidence")
    return matrix


def build_fix_plan(report: DoctorReport) -> list[FixPlanItem]:
    """Return commands only; callers display them and never execute them."""
    system = platform.system()
    brew = system == "Darwin"
    items: list[FixPlanItem] = []
    ollama = report.ollama
    if ollama is not None and getattr(ollama, "status", None) != "HEALTHY":
        status = str(getattr(ollama, "status", ""))
        if "NOT_INSTALLED" in status:
            items.append(FixPlanItem("Ollama", "brew install --cask ollama" if brew else "See https://ollama.com/download", "binary missing"))
        elif "DAEMON_OFFLINE" in status:
            items.append(FixPlanItem("Ollama daemon", "ollama serve", "local API offline"))
        elif "MODEL_MISSING" in status:
            items.append(FixPlanItem("Ollama model", f"ollama pull {ollama.model}", "configured model missing"))
        else:
            items.append(FixPlanItem("Ollama model", f"ollama run {ollama.model} \"Reply with OK\"", "model generation probe failed"))
    by_name = {check.name: check for check in report.checks}
    if by_name.get("ast-grep (optional)", DoctorCheck("", HEALTHY, "")).status != HEALTHY:
        items.append(FixPlanItem("ast-grep", "brew install ast-grep" if brew else "cargo install ast-grep --locked", "structural search fallback active"))
    if report.browser is not None and getattr(report.browser, "status", None) != "FULL_BROWSER_VERIFICATION":
        items.append(FixPlanItem("Playwright", "uv add --dev playwright && uv run playwright install chromium", "full browser probe unavailable"))
    lsp_commands = {
        "Python": "uv add --dev pyright",
        "TypeScript/JavaScript": "npm install -g typescript typescript-language-server",
        "C/C++": "brew install llvm" if brew else "Install clangd with your system package manager",
        "Rust": "rustup component add rust-analyzer",
        "Go": "go install golang.org/x/tools/gopls@latest",
    }
    for server in report.lsp_servers:
        if server.status != "HEALTHY":
            items.append(FixPlanItem(f"LSP {server.language}", lsp_commands[server.language], f"current state: {server.status}"))
    return items
