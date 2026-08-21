import urllib.request
from urllib.error import URLError

from skytrap.security.findings import ScanOutcome, SecurityFinding

SCANNER_NAME = "headers"
TIMEOUT_SECONDS = 10

# header -> (title, severity if missing, cwe, remediation)
_EXPECTED_HEADERS: dict[str, tuple[str, str, str, str]] = {
    "strict-transport-security": (
        "Missing HSTS", "medium", "CWE-319",
        "Add `Strict-Transport-Security: max-age=31536000; includeSubDomains` (HTTPS only).",
    ),
    "x-content-type-options": (
        "Missing X-Content-Type-Options", "low", "CWE-116",
        "Add `X-Content-Type-Options: nosniff` to prevent MIME-sniffing.",
    ),
    "content-security-policy": (
        "Missing Content-Security-Policy", "medium", "CWE-1021",
        "Add a Content-Security-Policy that restricts script/style/frame sources.",
    ),
    "x-frame-options": (
        "Missing X-Frame-Options / frame-ancestors", "medium", "CWE-1021",
        "Add `X-Frame-Options: DENY` or a CSP `frame-ancestors` directive to prevent clickjacking.",
    ),
    "referrer-policy": (
        "Missing Referrer-Policy", "low", "CWE-200",
        "Add `Referrer-Policy: strict-origin-when-cross-origin` (or stricter).",
    ),
    "permissions-policy": (
        "Missing Permissions-Policy", "info", "CWE-1021",
        "Add a Permissions-Policy restricting powerful browser features (camera, geolocation, ...).",
    ),
}


def scan_url(url: str) -> ScanOutcome:
    """A real HTTP GET, not a guess — headers are read from the actual response.
    Only ever called against a target the user named explicitly on the command
    line (see AuthorizationScope.for_explicit_target)."""
    try:
        request = urllib.request.Request(url, method="GET", headers={"User-Agent": "SkyTrap-Security/1"})
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310 - user-provided target
            headers = {k.lower(): v for k, v in response.getheaders()}
            status = response.status
    except (URLError, ValueError, TimeoutError, OSError) as exc:
        return ScanOutcome(scanner=SCANNER_NAME, ran=False, skipped_reason=f"could not reach {url}: {exc}")

    findings: list[SecurityFinding] = []
    for header, (title, severity, cwe, remediation) in _EXPECTED_HEADERS.items():
        if header not in headers:
            findings.append(
                SecurityFinding(
                    id=f"headers-{url}-{header}",
                    title=title,
                    category="http_headers",
                    severity=severity,
                    confidence="high",
                    impact=f"Response from {url} (HTTP {status}) does not set {header}.",
                    remediation=remediation,
                    scanner=SCANNER_NAME,
                    asset=url,
                    cwe=cwe,
                    verified="confirmed",
                )
            )

    if url.startswith("http://"):
        findings.append(
            SecurityFinding(
                id=f"headers-{url}-plaintext",
                title="Served over plain HTTP",
                category="http_headers",
                severity="high",
                confidence="high",
                impact="Traffic to this URL is unencrypted and can be intercepted or modified in transit.",
                remediation="Serve over HTTPS and redirect HTTP to HTTPS.",
                scanner=SCANNER_NAME,
                asset=url,
                cwe="CWE-319",
                verified="confirmed",
            )
        )

    return ScanOutcome(scanner=SCANNER_NAME, ran=True, findings=findings, detail=f"HTTP {status}")
