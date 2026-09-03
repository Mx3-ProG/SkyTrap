import json
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import httpx

from skytrap.autonomy.browser_verification import BrowserVerificationProvider
from skytrap.autonomy.evidence import ExecutionEvidence
from skytrap.autonomy.intent import HumanIntentEngine
from skytrap.autonomy.memory import WorkingMemory
from skytrap.autonomy.review import IndependentReviewer
from skytrap.bench import SCENARIOS, SkyTrapBench
from skytrap.core.context import WorkspaceContext
from skytrap.core.doctor import intelligence_health_report, run_doctor
from skytrap.intelligence.snapshot import build_repository_snapshot
from skytrap.intelligence.lsp import LanguageIntelligenceProvider
from skytrap.models.base import (
    ModelCapabilities,
    ModelCapability,
    ModelProfile,
    ModelProvider,
    ModelRole,
)
from skytrap.models.qualification import ModelQualificationSuite, QualificationStatus
from skytrap.models.catalog import ConfiguredModelCatalog
from skytrap.models.router import ModelRouter
from skytrap.technology.hardware import HardwareFit, HardwareProfile
from skytrap.technology.policy import SafeUpdatePolicy, UpdateStep
from skytrap.technology.watch import TechnologyWatch, UpdateCategory


class Provider(ModelProvider):
    engine = "LOCAL"

    def __init__(self, name="model", roles=None, answers=None):
        self.name = name
        self.roles = roles or set()
        self.answers = iter(answers or [])

    @property
    def capabilities(self):
        return ModelCapabilities(
            supported={
                ModelCapability.CHAT,
                ModelCapability.CODE_GENERATION,
                ModelCapability.REASONING,
                ModelCapability.STRUCTURED_OUTPUT,
            },
            context_window=8192,
        )

    @property
    def profile(self):
        return ModelProfile(
            name=self.name,
            provider="test",
            capabilities=self.capabilities,
            roles=self.roles,
        )

    def chat(self, messages):
        return next(self.answers)


def test_model_router_uses_roles_capabilities_configuration_and_qualification():
    fast = Provider("fast", {ModelRole.FAST})
    coder = Provider("coder", {ModelRole.CODING, ModelRole.REASONING})
    router = ModelRouter(
        [fast, coder],
        routes={ModelRole.CODING: "coder"},
        qualification_scores={"coder": 0.9},
    )

    assert router.route(ModelRole.CODING, require_qualified=True) is coder
    assert router.route(ModelRole.FAST) is fast


def test_model_qualification_runs_all_real_oracles_and_never_switches_on_novelty():
    answers = [
        '{"ok":true}',
        '{"type":"tool_call","tool":"read_file","arguments":{"path":"app.py"}}',
        "B.py",
        "return a + b",
        "AttributeError",
        "RABBIT-731",
        "SKYTRAP_OK",
    ]
    current = ModelQualificationSuite().run(Provider("current", answers=answers))
    candidate = current.model_copy(update={"model": "candidate"})

    assert len(current.probes) == 7
    assert current.qualification_status == QualificationStatus.QUALIFIED
    assert not ModelQualificationSuite.should_switch(current, candidate)


def test_execution_evidence_is_derived_from_runtime_events_only():
    memory = WorkingMemory(objective="fix")
    memory.record("tool_result", tool="read_file", path="app.py", success=True)
    memory.record("tool_result", tool="patch_file", path="app.py", success=True, is_new_file=False)
    memory.record("tool_result", tool="write_file", path="new.py", success=True, is_new_file=True)
    memory.record("tool_result", tool="delete_file", path="old.py", success=True, is_delete=True)

    evidence = ExecutionEvidence.from_memory(memory)

    assert evidence.files_read == ["app.py"]
    assert evidence.files_modified == ["app.py"]
    assert evidence.files_created == ["new.py"]
    assert evidence.files_deleted == ["old.py"]
    assert evidence.patches_applied == ["app.py"]


def test_independent_reviewer_rejects_test_only_fix(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_login.py").write_text("def test_login(): pass\n")
    workspace = WorkspaceContext(path=tmp_path, name="repo", is_git=False)
    snapshot = build_repository_snapshot(workspace)
    intent = HumanIntentEngine().normalize("corrige le login")

    review = IndependentReviewer().review(
        original_request=intent.raw_input,
        intent=intent,
        snapshot=snapshot,
        diff="-assert login()\n+assert True\n",
        verification_results=[{"success": True, "results": [{}]}],
        diagnostics=[],
        evidence=ExecutionEvidence(files_modified=["tests/test_login.py"], tests_run=[{}]),
    )

    assert not review.passed
    assert any(item.category == "test_integrity" for item in review.findings)


def test_browser_provider_performs_real_http_navigation(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<h1>Rabbit</h1>")
    handler = lambda *args, **kwargs: SimpleHTTPRequestHandler(*args, directory=str(tmp_path), **kwargs)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    provider = BrowserVerificationProvider()
    monkeypatch.setattr(provider, "available", lambda: False)
    workspace = WorkspaceContext(path=tmp_path, name="web", is_git=False)
    snapshot = build_repository_snapshot(workspace)
    monkeypatch.setenv("SKYTRAP_BROWSER_VERIFY_URL", f"http://127.0.0.1:{server.server_port}")
    try:
        result = provider.verify(workspace, snapshot, timeout=2)
    finally:
        server.shutdown()
        server.server_close()

    assert result.success
    assert result.status_code == 200
    assert "HTTP navigation passed" in result.detail


def test_repository_snapshot_contains_symbols_routes_and_relationships(tmp_path):
    (tmp_path / "app.py").write_text(
        "from lib import helper\n@app.get('/login')\ndef login(): return helper()\n"
    )
    (tmp_path / "lib.py").write_text("def helper(): return 1\n")
    snapshot = build_repository_snapshot(
        WorkspaceContext(path=tmp_path, name="repo", is_git=False)
    )

    assert "app.py" in snapshot.modules
    assert "function:login" in snapshot.symbols["app.py"]
    assert "/login" in snapshot.routes
    assert snapshot.imports["app.py"]
    assert snapshot.dependency_relationships == snapshot.imports


def test_lsp_bridge_performs_real_json_rpc_round_trip(tmp_path):
    (tmp_path / "app.py").write_text("def login(): pass\n")
    server = tmp_path / "fake-lsp"
    server.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "while True:\n"
        "    headers = {}\n"
        "    while True:\n"
        "        line = sys.stdin.buffer.readline()\n"
        "        if not line: raise SystemExit\n"
        "        if line in (b'\\r\\n', b'\\n'): break\n"
        "        key, value = line.decode().split(':', 1); headers[key.lower()] = value.strip()\n"
        "    msg = json.loads(sys.stdin.buffer.read(int(headers['content-length'])))\n"
        "    if 'id' not in msg: continue\n"
        "    result = {'capabilities': {}} if msg['method'] == 'initialize' else [{'name': 'login', 'kind': 12}]\n"
        "    raw = json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': result}).encode()\n"
        "    sys.stdout.buffer.write(f'Content-Length: {len(raw)}\\r\\n\\r\\n'.encode() + raw); sys.stdout.buffer.flush()\n"
    )
    server.chmod(0o755)
    provider = LanguageIntelligenceProvider(detected={"python": str(server)})
    workspace = WorkspaceContext(path=tmp_path, name="repo", is_git=False)

    result = provider.document_symbols(workspace, "app.py", language="python")

    assert result.supported
    assert result.data[0]["name"] == "login"


def test_hardware_fit_and_safe_update_pipeline_are_conservative():
    hardware = HardwareProfile(
        os="Darwin", architecture="arm64", cpu="Apple", ram_gb=16,
        unified_memory=True, disk_available_gb=100,
    )
    plan = SafeUpdatePolicy().plan("tree-sitter", major=True)

    assert hardware.fit_model(8) == HardwareFit.RECOMMENDED
    assert hardware.fit_model(32) == HardwareFit.TOO_HEAVY
    assert plan.requires_branch and plan.requires_approval
    assert plan.automatic_upgrade is False
    assert plan.steps[-2:] == [UpdateStep.UPGRADE, UpdateStep.ROLLBACK]


class Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"info": {"version": "999.0", "package_url": "https://pypi.org/project/pkg/"}}


class Client:
    def get(self, url):
        assert url.startswith("https://pypi.org/pypi/")
        return Response()


def test_technology_watch_uses_structured_official_sources(monkeypatch):
    monkeypatch.setenv("SKYTRAP_MODEL_CANDIDATES", "candidate-coder:7b")
    report = TechnologyWatch(client=Client(), catalogs=[ConfiguredModelCatalog()]).check()

    assert report.sources_reached == len(report.findings) - 1
    assert report.findings[0].category == UpdateCategory.MODELS
    assert report.findings[0].benchmark_required
    assert all(item.source.startswith("https://pypi.org/") for item in report.findings[1:])


def test_skytrap_bench_runs_every_fixture_scenario():
    report = SkyTrapBench().run()

    assert len(report.scenarios) == len(SCENARIOS) >= 13
    assert report.success_rate == 1
    assert report.reviewer_catches == 1


def test_doctor_produces_evidence_based_health_dimensions(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYTRAP_STATE_DIR", str(tmp_path / "state"))
    workspace = WorkspaceContext(path=tmp_path, name="repo", is_git=False)
    report = run_doctor(workspace)
    health = intelligence_health_report(report)

    assert 0 <= report.readiness_score <= 10
    assert len(health) == 11
    assert all(0 <= item.score <= 10 and item.evidence for item in health)
