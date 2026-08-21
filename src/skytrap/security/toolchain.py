import shutil

# Security-specific tools SkyTrap knows how to use when present — never assumed to
# be installed. Distinct from core.toolchain's language build tools (some overlap,
# e.g. docker/npm, is expected — a dependency-audit tool is also a package manager).
KNOWN_SECURITY_TOOLS = (
    "gitleaks", "trufflehog",
    "semgrep", "bandit", "brakeman",
    "pip-audit", "npm", "pnpm", "yarn", "cargo-audit", "bundler-audit", "govulncheck", "composer",
    "osv-scanner", "trivy", "grype", "syft", "dockle", "hadolint",
    "nmap", "arp-scan",
    "dig", "nslookup", "host", "drill",
    "openssl", "testssl.sh", "sslyze",
    "docker",
    "kube-linter", "kubescape", "checkov", "tfsec",
    "zap-cli", "nikto", "nuclei",
    "tshark", "tcpdump", "wireshark",
    "ip", "ifconfig", "netstat", "ss", "traceroute",
)


def detect_security_toolchain(tools: tuple[str, ...] = KNOWN_SECURITY_TOOLS) -> dict[str, str | None]:
    """Real `which` lookups — a tool only counts as available if it's actually on
    PATH. Every scanner that wants to shell out to one of these must check this
    first and report an honest "skipped: tool not installed" rather than silently
    doing nothing and calling it a completed scan."""
    return {name: shutil.which(name) for name in tools}
