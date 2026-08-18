import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pytest

from skytrap.core.context import WorkspaceContext
from skytrap.tools.skills.seo_audit.tool import SeoAuditTool


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - matches base class signature
        pass


@pytest.fixture
def served_url(tmp_path):
    """A real local HTTP server backed by tmp_path — not mocked, exercises the
    tool's actual httpx fetch + HTML parsing against real bytes on the wire."""

    def handler_factory(*args, **kwargs):
        return _QuietHandler(*args, directory=str(tmp_path), **kwargs)

    server = HTTPServer(("127.0.0.1", 0), handler_factory)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(timeout=2)


def _workspace(tmp_path: Path) -> WorkspaceContext:
    return WorkspaceContext(path=tmp_path.resolve(), name=tmp_path.name, is_git=False)


GOOD_PAGE = """<!DOCTYPE html>
<html>
<head>
  <title>A concise, well-sized page title</title>
  <meta name="description" content="A description that is short enough to fit the recommended length easily.">
  <link rel="canonical" href="http://127.0.0.1/index.html">
  <meta property="og:title" content="A concise, well-sized page title">
  <meta property="og:description" content="A description that is short enough.">
  <meta property="og:image" content="http://127.0.0.1/image.png">
</head>
<body>
  <h1>Main heading</h1>
  <h2>Subheading</h2>
</body>
</html>"""

BAD_PAGE = """<!DOCTYPE html>
<html>
<head></head>
<body>
  <h2>No h1 here</h2>
</body>
</html>"""

MULTI_H1_PAGE = """<!DOCTYPE html>
<html><head><title>x</title></head>
<body><h1>First</h1><h1>Second</h1></body></html>"""


def test_good_page_with_robots_and_sitemap_has_no_findings(tmp_path, served_url):
    (tmp_path / "index.html").write_text(GOOD_PAGE)
    (tmp_path / "robots.txt").write_text("User-agent: *\nAllow: /")
    (tmp_path / "sitemap.xml").write_text("<urlset></urlset>")

    result = SeoAuditTool().execute(_workspace(tmp_path), {"url": f"{served_url}/index.html"})

    assert result.success
    assert "No issues found" in result.output


def test_bad_page_flags_missing_title_description_canonical_og_h1(tmp_path, served_url):
    (tmp_path / "index.html").write_text(BAD_PAGE)
    # deliberately no robots.txt / sitemap.xml either

    result = SeoAuditTool().execute(_workspace(tmp_path), {"url": f"{served_url}/index.html"})

    assert result.success  # tool ran fine; findings are the (successful) result
    assert "Missing <title> tag" in result.output
    assert "Missing meta description" in result.output
    assert "canonical" in result.output
    assert "og:title" in result.output
    assert "No <h1> found" in result.output
    assert "robots.txt not found" in result.output
    assert "sitemap.xml not found" in result.output


def test_multiple_h1_flagged(tmp_path, served_url):
    (tmp_path / "index.html").write_text(MULTI_H1_PAGE)

    result = SeoAuditTool().execute(_workspace(tmp_path), {"url": f"{served_url}/index.html"})

    assert result.success
    assert "2 <h1> tags found" in result.output


def test_rejects_non_local_url(tmp_path):
    result = SeoAuditTool().execute(_workspace(tmp_path), {"url": "https://example.com"})
    assert not result.success
    assert "localhost" in result.output


def test_unreachable_server_fails_gracefully(tmp_path):
    result = SeoAuditTool().execute(_workspace(tmp_path), {"url": "http://127.0.0.1:1/nope"})
    assert not result.success
    assert "Could not fetch" in result.output


def test_missing_url_argument(tmp_path):
    result = SeoAuditTool().execute(_workspace(tmp_path), {})
    assert not result.success
    assert "Invalid arguments" in result.output
