from urllib.parse import urljoin, urlparse

import httpx
from pydantic import ValidationError

from skytrap.core.context import WorkspaceContext
from skytrap.tools.base import Tool, ToolResult
from skytrap.tools.registry import RegistryContext, register_tool
from skytrap.tools.skills.seo_audit.parser import SeoHTMLParser
from skytrap.tools.skills.seo_audit.schema import SeoAuditInput
from skytrap.tools.verification import validate_local_url

FETCH_TIMEOUT_SECONDS = 10
TITLE_MAX_RECOMMENDED = 60
DESCRIPTION_MAX_RECOMMENDED = 160
OPEN_GRAPH_TAGS = ("og:title", "og:description", "og:image")


def _check_page(parser: SeoHTMLParser) -> list[str]:
    findings = []

    title = (parser.title or "").strip()
    if not title:
        findings.append("Missing <title> tag")
    elif len(title) > TITLE_MAX_RECOMMENDED:
        findings.append(
            f"<title> is {len(title)} chars, longer than the ~{TITLE_MAX_RECOMMENDED} char recommendation"
        )

    description = parser.meta.get("description")
    if not description:
        findings.append("Missing meta description")
    elif len(description) > DESCRIPTION_MAX_RECOMMENDED:
        findings.append(
            f"Meta description is {len(description)} chars, longer than the "
            f"~{DESCRIPTION_MAX_RECOMMENDED} char recommendation"
        )

    if not parser.meta.get("canonical_href"):
        findings.append("Missing <link rel=\"canonical\">")

    for tag in OPEN_GRAPH_TAGS:
        if not parser.meta.get(tag):
            findings.append(f"Missing Open Graph tag: {tag}")

    h1_count = parser.heading_counts.get("h1", 0)
    if h1_count == 0:
        findings.append("No <h1> found on the page")
    elif h1_count > 1:
        findings.append(f"{h1_count} <h1> tags found — a page should typically have exactly one")

    return findings


def _check_well_known_file(client: httpx.Client, base_url: str, filename: str) -> str | None:
    url = urljoin(base_url, filename)
    try:
        response = client.get(url, timeout=FETCH_TIMEOUT_SECONDS)
    except httpx.HTTPError:
        return f"{filename} could not be fetched"
    if response.status_code != 200:
        return f"{filename} not found (HTTP {response.status_code})"
    return None


class SeoAuditTool(Tool):
    name = "seo_audit"
    description = (
        "Technical SEO audit of a page already being served by a local dev server: title/"
        "meta description length, canonical link, Open Graph tags, heading hierarchy (h1 "
        "count), robots.txt and sitemap.xml presence. Complements lighthouse_audit, which "
        "already covers a general SEO score plus performance/accessibility/best-practices — "
        "this tool checks specifics Lighthouse doesn't. The dev server must already be "
        'running. Arguments: {"url": "<http://localhost:PORT/...>"}'
    )

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        try:
            parsed = SeoAuditInput.model_validate(arguments)
        except ValidationError as exc:
            return ToolResult(success=False, output=f"Invalid arguments: {exc}")

        ok, error = validate_local_url(parsed.url)
        if not ok:
            return ToolResult(success=False, output=error)

        try:
            with httpx.Client(follow_redirects=True) as client:
                response = client.get(parsed.url, timeout=FETCH_TIMEOUT_SECONDS)
                response.raise_for_status()

                page_parser = SeoHTMLParser()
                page_parser.feed(response.text)
                findings = _check_page(page_parser)

                base_url = f"{urlparse(parsed.url).scheme}://{urlparse(parsed.url).netloc}/"
                for filename in ("robots.txt", "sitemap.xml"):
                    finding = _check_well_known_file(client, base_url, filename)
                    if finding:
                        findings.append(finding)
        except httpx.HTTPError as exc:
            return ToolResult(success=False, output=f"Could not fetch {parsed.url}: {exc}")

        if not findings:
            return ToolResult(
                success=True,
                output=(
                    "No issues found among title/meta description/canonical/Open Graph/"
                    "heading hierarchy/robots.txt/sitemap.xml checks."
                ),
            )
        return ToolResult(success=True, output="SEO findings:\n" + "\n".join(f"- {f}" for f in findings))


@register_tool
def _build_seo_audit_tool(context: RegistryContext) -> Tool:
    return SeoAuditTool()
