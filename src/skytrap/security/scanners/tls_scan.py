import datetime
import socket
import ssl

from skytrap.security.findings import ScanOutcome, SecurityFinding

SCANNER_NAME = "tls"
TIMEOUT_SECONDS = 10
_CERT_DATE_FORMAT = "%b %d %H:%M:%S %Y %Z"


def scan_host(host: str, port: int = 443) -> ScanOutcome:
    """A real TLS handshake against the target the user named explicitly — reads
    the actual negotiated protocol/cipher and the actual server certificate, never
    a guess. Nothing here modifies or floods the target: one connection, one
    handshake, then closed."""
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT_SECONDS) as sock:
            with context.wrap_socket(sock, server_hostname=host) as tls_sock:
                cert = tls_sock.getpeercert()
                protocol = tls_sock.version()
                cipher_name, _, _ = tls_sock.cipher()
    except ssl.SSLCertVerificationError as exc:
        return ScanOutcome(
            scanner=SCANNER_NAME,
            ran=True,
            findings=[
                SecurityFinding(
                    id=f"tls-{host}-verify",
                    title="Certificate verification failed",
                    category="tls",
                    severity="high",
                    confidence="high",
                    impact=f"The certificate presented by {host}:{port} did not verify: {exc.verify_message}.",
                    remediation="Install a valid certificate from a trusted CA covering this hostname, "
                    "and ensure the full chain is served.",
                    scanner=SCANNER_NAME,
                    asset=f"{host}:{port}",
                    verified="confirmed",
                )
            ],
        )
    except (socket.timeout, socket.gaierror, ConnectionRefusedError, OSError) as exc:
        return ScanOutcome(scanner=SCANNER_NAME, ran=False, skipped_reason=f"could not connect to {host}:{port}: {exc}")

    findings: list[SecurityFinding] = []

    not_after = cert.get("notAfter")
    if not_after:
        expiry = datetime.datetime.strptime(not_after, _CERT_DATE_FORMAT).replace(tzinfo=datetime.timezone.utc)
        days_left = (expiry - datetime.datetime.now(datetime.timezone.utc)).days
        if days_left < 0:
            findings.append(
                SecurityFinding(
                    id=f"tls-{host}-expired",
                    title="Certificate expired",
                    category="tls",
                    severity="critical",
                    confidence="high",
                    impact=f"Certificate for {host} expired {-days_left} day(s) ago.",
                    remediation="Renew the certificate immediately.",
                    scanner=SCANNER_NAME,
                    asset=f"{host}:{port}",
                    cwe="CWE-298",
                    verified="confirmed",
                )
            )
        elif days_left < 14:
            findings.append(
                SecurityFinding(
                    id=f"tls-{host}-expiring",
                    title="Certificate expiring soon",
                    category="tls",
                    severity="medium",
                    confidence="high",
                    impact=f"Certificate for {host} expires in {days_left} day(s).",
                    remediation="Renew the certificate before expiry (automate renewal if possible).",
                    scanner=SCANNER_NAME,
                    asset=f"{host}:{port}",
                    verified="confirmed",
                )
            )

    if protocol in ("TLSv1", "TLSv1.1", "SSLv3", "SSLv2"):
        findings.append(
            SecurityFinding(
                id=f"tls-{host}-legacy-protocol",
                title=f"Legacy TLS protocol negotiated: {protocol}",
                category="tls",
                severity="high",
                confidence="high",
                impact=f"{host}:{port} negotiated {protocol}, which has known weaknesses and is "
                "deprecated by major browsers/clients.",
                remediation="Disable protocol versions below TLS 1.2 (prefer requiring TLS 1.2+).",
                scanner=SCANNER_NAME,
                asset=f"{host}:{port}",
                cwe="CWE-327",
                verified="confirmed",
            )
        )

    detail = f"{protocol}, {cipher_name}, expires {not_after}"
    return ScanOutcome(scanner=SCANNER_NAME, ran=True, findings=findings, detail=detail)
