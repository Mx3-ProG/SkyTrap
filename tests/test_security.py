from skytrap.core.context import WorkspaceContext
from skytrap.security.context import AuthorizationScope, SecurityContext
from skytrap.security.report import SecurityReport
from skytrap.security.scanners import network, secrets, static_analysis
from skytrap.security.findings import SecurityFinding


def _workspace(tmp_path):
    return WorkspaceContext(path=tmp_path, name=tmp_path.name, is_git=False)


def _context(tmp_path):
    return SecurityContext(workspace=_workspace(tmp_path), scope=AuthorizationScope())


# --- secrets ---------------------------------------------------------------


def test_secrets_scanner_finds_aws_key_and_redacts_it(tmp_path):
    (tmp_path / "config.py").write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')

    outcome = secrets.scan(_context(tmp_path), toolchain={})

    assert outcome.ran is True
    assert len(outcome.findings) == 1
    finding = outcome.findings[0]
    assert finding.severity == "critical"
    assert finding.file == "config.py"
    assert finding.line == 1
    assert "AKIAABCDEFGHIJKLMNOP" not in finding.evidence
    assert finding.evidence.startswith("AKIA")


def test_secrets_scanner_flags_committed_private_key_filename(tmp_path):
    (tmp_path / "id_rsa").write_text("not a real key, just a filename test\n")

    outcome = secrets.scan(_context(tmp_path), toolchain={})

    assert any(f.file == "id_rsa" for f in outcome.findings)


def test_secrets_scanner_clean_repo_has_no_findings(tmp_path):
    (tmp_path / "main.py").write_text("print('hello world')\n")

    outcome = secrets.scan(_context(tmp_path), toolchain={})

    assert outcome.ran is True
    assert outcome.findings == []


# --- static analysis ---------------------------------------------------------


def test_static_analysis_flags_eval_in_python(tmp_path):
    (tmp_path / "app.py").write_text("result = eval(user_input)\n")

    outcome = static_analysis.scan(_context(tmp_path), toolchain={})

    assert outcome.ran is True
    assert any("eval" in f.title.lower() for f in outcome.findings)
    assert all(f.confidence != "high" or f.verified != "confirmed" for f in outcome.findings)


def test_static_analysis_no_recognized_language_is_skipped(tmp_path):
    (tmp_path / "README.md").write_text("hello\n")

    outcome = static_analysis.scan(_context(tmp_path), toolchain={})

    assert outcome.ran is False
    assert outcome.skipped_reason is not None


# --- network -----------------------------------------------------------------


def test_network_cidr_analysis_computes_real_ranges():
    outcome = network.analyze_cidr("192.168.1.0/24")

    assert outcome.ran is True
    assert "192.168.1.0" in outcome.detail
    assert "192.168.1.255" in outcome.detail
    assert outcome.findings == []  # private range — no "public range" finding


def test_network_public_range_flagged_as_info():
    outcome = network.analyze_cidr("8.8.8.0/24")

    assert outcome.ran is True
    assert len(outcome.findings) == 1
    assert outcome.findings[0].severity == "info"


def test_network_invalid_cidr_is_skipped():
    outcome = network.analyze_cidr("not-a-cidr")

    assert outcome.ran is False
    assert outcome.skipped_reason is not None


# --- report --------------------------------------------------------------


def test_report_sorts_findings_by_severity_and_counts_correctly():
    from skytrap.security.findings import ScanOutcome

    findings = [
        SecurityFinding(
            id="1", title="low one", category="x", severity="low", confidence="low",
            impact="x", remediation="x", scanner="test",
        ),
        SecurityFinding(
            id="2", title="critical one", category="x", severity="critical", confidence="high",
            impact="x", remediation="x", scanner="test",
        ),
    ]
    report = SecurityReport(target="t", outcomes=[ScanOutcome(scanner="test", ran=True, findings=findings)])

    assert report.findings[0].severity == "critical"
    assert report.counts_by_severity()["critical"] == 1
    assert report.counts_by_severity()["low"] == 1
    assert report.worst_severity() == "critical"


def test_report_fails_threshold_only_when_severity_meets_or_exceeds():
    from skytrap.security.findings import ScanOutcome

    medium_finding = SecurityFinding(
        id="1", title="medium", category="x", severity="medium", confidence="high",
        impact="x", remediation="x", scanner="test",
    )
    report = SecurityReport(target="t", outcomes=[ScanOutcome(scanner="test", ran=True, findings=[medium_finding])])

    assert report.fails_threshold("high") is False
    assert report.fails_threshold("medium") is True
    assert report.fails_threshold("low") is True


def test_report_no_findings_does_not_fail_threshold():
    report = SecurityReport(target="t", outcomes=[])
    assert report.fails_threshold("critical") is False


# --- authorization scope --------------------------------------------------


def test_authorization_scope_local_repo_default_allowed():
    scope = AuthorizationScope()
    assert scope.local_repository is True
    assert scope.allows_network_target("example.com") is False


def test_authorization_scope_for_explicit_target_allows_only_that_target():
    scope = AuthorizationScope.for_explicit_target("example.com")
    assert scope.allows_network_target("example.com") is True
    assert scope.allows_network_target("other.com") is False
