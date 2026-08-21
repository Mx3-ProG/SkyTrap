import shutil
import socket
import subprocess

from skytrap.security.findings import ScanOutcome, SecurityFinding

SCANNER_NAME = "dns"
TIMEOUT_SECONDS = 10
_RECORD_TYPES = ("A", "AAAA", "MX", "TXT", "NS", "CAA")


def _dig(domain: str, record_type: str) -> str | None:
    dig_path = shutil.which("dig")
    if not dig_path:
        return None
    try:
        result = subprocess.run(
            [dig_path, "+short", record_type, domain], capture_output=True, text=True, timeout=TIMEOUT_SECONDS
        )
    except (subprocess.SubprocessError, OSError):
        return None
    return result.stdout.strip()


def scan_domain(domain: str) -> ScanOutcome:
    """Real DNS lookups against the domain the user named explicitly. Prefers `dig`
    (richer record types) and falls back to Python's socket resolver for A/AAAA
    only when dig isn't installed — never fabricates a record."""
    records: dict[str, str] = {}
    dig_available = shutil.which("dig") is not None

    if dig_available:
        for record_type in _RECORD_TYPES:
            output = _dig(domain, record_type)
            if output:
                records[record_type] = output
    else:
        try:
            addresses = socket.getaddrinfo(domain, None)
            ipv4 = sorted({a[4][0] for a in addresses if a[0] == socket.AF_INET})
            ipv6 = sorted({a[4][0] for a in addresses if a[0] == socket.AF_INET6})
            if ipv4:
                records["A"] = "\n".join(ipv4)
            if ipv6:
                records["AAAA"] = "\n".join(ipv6)
        except socket.gaierror as exc:
            return ScanOutcome(scanner=SCANNER_NAME, ran=False, skipped_reason=f"DNS resolution failed: {exc}")

    if not records:
        return ScanOutcome(scanner=SCANNER_NAME, ran=True, findings=[], detail="no records found")

    findings: list[SecurityFinding] = []
    spf_present = any("v=spf1" in v.lower() for v in [records.get("TXT", "")])
    dmarc_output = _dig(f"_dmarc.{domain}", "TXT") if dig_available else None

    if records.get("MX") and not spf_present:
        findings.append(
            SecurityFinding(
                id=f"dns-{domain}-spf",
                title="No SPF record found",
                category="dns",
                severity="medium",
                confidence="medium",
                impact="Without SPF, receiving mail servers have no policy to check whether mail "
                "claiming to be from this domain was sent from an authorized server.",
                remediation="Publish a TXT record: `v=spf1 include:<your provider> -all`.",
                scanner=SCANNER_NAME,
                asset=domain,
                cwe="CWE-290",
                verified="likely" if dig_available else "unverified",
            )
        )
    if records.get("MX") and dig_available and not dmarc_output:
        findings.append(
            SecurityFinding(
                id=f"dns-{domain}-dmarc",
                title="No DMARC record found",
                category="dns",
                severity="medium",
                confidence="high",
                impact="Without DMARC, spoofed mail claiming to be from this domain has no enforcement "
                "policy and no reporting to the domain owner.",
                remediation=f"Publish a TXT record at _dmarc.{domain}: `v=DMARC1; p=quarantine; ...`.",
                scanner=SCANNER_NAME,
                asset=domain,
                cwe="CWE-290",
                verified="confirmed",
            )
        )

    detail = ", ".join(f"{k}: {v.splitlines()[0]}" for k, v in records.items())
    return ScanOutcome(scanner=SCANNER_NAME, ran=True, findings=findings, detail=detail)
