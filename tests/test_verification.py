from skytrap.tools.verification import (
    _summarize_axe_report,
    _summarize_lighthouse_report,
    validate_local_url,
)


def test_validate_local_url_accepts_localhost():
    ok, error = validate_local_url("http://localhost:3000/")
    assert ok
    assert error == ""


def test_validate_local_url_accepts_127_0_0_1():
    ok, _ = validate_local_url("http://127.0.0.1:8080")
    assert ok


def test_validate_local_url_rejects_external_host():
    ok, error = validate_local_url("https://example.com")
    assert not ok
    assert "localhost" in error


def test_validate_local_url_rejects_bad_scheme():
    ok, error = validate_local_url("ftp://localhost")
    assert not ok
    assert "http" in error


def test_summarize_lighthouse_report_extracts_scores_and_issues():
    report = {
        "categories": {
            "performance": {"title": "Performance", "score": 0.85},
            "accessibility": {"title": "Accessibility", "score": 0.5},
        },
        "audits": {
            "uses-alt-text": {"title": "Images have alt text", "score": 0.0},
            "good-audit": {"title": "Everything fine here", "score": 1.0},
            "informative-audit": {"title": "No numeric score", "score": None},
        },
    }
    summary = _summarize_lighthouse_report(report)
    assert "Performance: 85/100" in summary
    assert "Accessibility: 50/100" in summary
    assert "Images have alt text" in summary
    assert "Everything fine here" not in summary


def test_summarize_axe_report_no_violations():
    assert _summarize_axe_report([{"violations": []}]) == "No accessibility violations found."


def test_summarize_axe_report_lists_violations():
    data = [
        {
            "violations": [
                {
                    "id": "image-alt",
                    "impact": "critical",
                    "help": "Images must have alt text",
                    "nodes": [{"target": ["img.logo"]}],
                }
            ]
        }
    ]
    summary = _summarize_axe_report(data)
    assert "1 accessibility violation" in summary
    assert "image-alt" in summary
    assert "img.logo" in summary
