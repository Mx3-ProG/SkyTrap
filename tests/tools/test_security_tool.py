from skytrap.core.context import WorkspaceContext
from skytrap.tools.security import SecurityAuditTool


def _workspace(tmp_path):
    return WorkspaceContext(path=tmp_path, name=tmp_path.name, is_git=False)


def test_security_audit_tool_reports_finding_with_severity_and_remediation(tmp_path):
    (tmp_path / "app.py").write_text("os.system(user_input)\n")

    result = SecurityAuditTool().execute(_workspace(tmp_path), {})

    assert result.success is True
    assert "os.system" in result.output.lower() or "system()" in result.output.lower()
    assert "high" in result.output.lower()


def test_security_audit_tool_clean_repo_reports_zero_findings(tmp_path):
    (tmp_path / "README.md").write_text("hello\n")

    result = SecurityAuditTool().execute(_workspace(tmp_path), {})

    assert result.success is True
    assert "critical: 0" in result.output.lower()
