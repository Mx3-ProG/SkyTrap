import json
import subprocess
from typing import Callable

CommandRunner = Callable[[list[str]], subprocess.CompletedProcess]


class TailscaleError(Exception):
    """Raised with an actionable message — missing binary, HTTPS not enabled in
    the admin console, malformed status output — never a raw traceback, since
    this runs from a CLI command a human is watching."""


def _default_runner(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True)


def enable_serve(port: int, run: CommandRunner = _default_runner) -> str:
    """Configures `tailscale serve` to reverse-proxy this tailnet device's HTTPS
    hostname to the local SkyTrap server. `--bg` makes this persist independently
    of the calling process (and across tailscaled restarts) — that's the point:
    permanent phone access as long as the Mac stays on, not tied to how long
    `skytrap serve-web` happens to run. `run` is injected (same pattern as the
    `confirm`/`on_write` callbacks elsewhere in this project) so this is testable
    with a fake command runner, without the real `tailscale` binary installed.
    Returns the real https://<magicdns-name>/ URL to open on the phone.
    """
    try:
        serve_result = run(["tailscale", "serve", "--bg", "--https=443", f"localhost:{port}"])
    except FileNotFoundError as exc:
        raise TailscaleError(
            "The `tailscale` command wasn't found. Install Tailscale first "
            "(https://tailscale.com/download) and make sure it's logged in "
            "(`tailscale status`)."
        ) from exc

    if serve_result.returncode != 0:
        detail = serve_result.stderr.strip() or serve_result.stdout.strip()
        raise TailscaleError(f"`tailscale serve` failed: {detail}")

    status_result = run(["tailscale", "status", "--json"])
    if status_result.returncode != 0:
        detail = status_result.stderr.strip() or status_result.stdout.strip()
        raise TailscaleError(f"`tailscale status` failed: {detail}")

    try:
        status = json.loads(status_result.stdout)
        dns_name = status["Self"]["DNSName"].rstrip(".")
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise TailscaleError("Could not parse `tailscale status --json` output.") from exc

    if not dns_name:
        raise TailscaleError("Tailscale reported an empty DNS name — is MagicDNS enabled?")

    return f"https://{dns_name}/"
