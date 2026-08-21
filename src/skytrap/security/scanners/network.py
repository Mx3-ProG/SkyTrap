import ipaddress
import shutil
import subprocess

from skytrap.security.findings import ScanOutcome, SecurityFinding

SCANNER_NAME = "network"
TIMEOUT_SECONDS = 10


def analyze_cidr(cidr: str) -> ScanOutcome:
    """Pure CIDR math (ipaddress stdlib) — no packets sent, always safe regardless
    of authorization scope. Flags a 0.0.0.0/0-shaped input and non-RFC1918 ranges
    as informational context, not a "vulnerability"."""
    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError as exc:
        return ScanOutcome(scanner=SCANNER_NAME, ran=False, skipped_reason=f"invalid CIDR: {exc}")

    hosts = list(network.hosts())
    findings: list[SecurityFinding] = []
    if not network.is_private and network.version == 4:
        findings.append(
            SecurityFinding(
                id=f"network-{cidr}-public-range",
                title="Public (non-RFC1918) IPv4 range",
                category="network",
                severity="info",
                confidence="high",
                impact=f"{cidr} is not a private range — hosts in it are potentially internet-routable.",
                remediation="Confirm this range's exposure is intentional; restrict with firewall/"
                "security-group rules if not.",
                scanner=SCANNER_NAME,
                asset=cidr,
                verified="confirmed",
            )
        )

    detail = (
        f"network={network.network_address} broadcast={network.broadcast_address} "
        f"hosts={hosts[0]}-{hosts[-1]}" if hosts else f"network={network.network_address} (no usable hosts)"
    )
    return ScanOutcome(scanner=SCANNER_NAME, ran=True, findings=findings, detail=detail)


def list_local_interfaces() -> ScanOutcome:
    """Lists this machine's own network interfaces via `ip`/`ifconfig` — local,
    read-only, never touches another host. No active scanning of other machines
    happens here or anywhere in this scanner; that's explicitly out of MVP scope
    (Nmap-based host discovery requires the user to name and authorize a target)."""
    for tool in ("ip", "ifconfig"):
        path = shutil.which(tool)
        if not path:
            continue
        args = [path, "addr"] if tool == "ip" else [path]
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        except (subprocess.SubprocessError, OSError):
            continue
        return ScanOutcome(scanner=SCANNER_NAME, ran=True, findings=[], detail=result.stdout.strip()[:2000])

    return ScanOutcome(scanner=SCANNER_NAME, ran=False, skipped_reason="neither `ip` nor `ifconfig` is available")
