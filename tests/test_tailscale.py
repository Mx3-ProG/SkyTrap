import json
import subprocess

import pytest

from skytrap.core.tailscale import TailscaleError, enable_serve


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_enable_serve_builds_the_right_commands_and_returns_the_url():
    calls: list[list[str]] = []

    def fake_run(command: list[str]) -> subprocess.CompletedProcess:
        calls.append(command)
        if command[:2] == ["tailscale", "serve"]:
            return _completed(0)
        if command[:2] == ["tailscale", "status"]:
            return _completed(0, stdout=json.dumps({"Self": {"DNSName": "my-mac.tailnet-name.ts.net."}}))
        raise AssertionError(f"unexpected command: {command}")

    url = enable_serve(8000, run=fake_run)

    assert url == "https://my-mac.tailnet-name.ts.net/"
    assert calls[0] == ["tailscale", "serve", "--bg", "--https=443", "localhost:8000"]
    assert calls[1] == ["tailscale", "status", "--json"]


def test_enable_serve_raises_a_clear_error_when_the_binary_is_missing():
    def fake_run(command: list[str]) -> subprocess.CompletedProcess:
        raise FileNotFoundError("tailscale")

    with pytest.raises(TailscaleError, match="wasn't found"):
        enable_serve(8000, run=fake_run)


def test_enable_serve_raises_on_serve_failure_with_stderr_detail():
    def fake_run(command: list[str]) -> subprocess.CompletedProcess:
        if command[:2] == ["tailscale", "serve"]:
            return _completed(1, stderr="HTTPS is not enabled for this tailnet")
        raise AssertionError(f"unexpected command: {command}")

    with pytest.raises(TailscaleError, match="HTTPS is not enabled"):
        enable_serve(8000, run=fake_run)


def test_enable_serve_raises_on_unparseable_status_output():
    def fake_run(command: list[str]) -> subprocess.CompletedProcess:
        if command[:2] == ["tailscale", "serve"]:
            return _completed(0)
        if command[:2] == ["tailscale", "status"]:
            return _completed(0, stdout="not json")
        raise AssertionError(f"unexpected command: {command}")

    with pytest.raises(TailscaleError, match="Could not parse"):
        enable_serve(8000, run=fake_run)


def test_enable_serve_raises_on_missing_dns_name():
    def fake_run(command: list[str]) -> subprocess.CompletedProcess:
        if command[:2] == ["tailscale", "serve"]:
            return _completed(0)
        if command[:2] == ["tailscale", "status"]:
            return _completed(0, stdout=json.dumps({"Self": {"DNSName": ""}}))
        raise AssertionError(f"unexpected command: {command}")

    with pytest.raises(TailscaleError, match="empty DNS name"):
        enable_serve(8000, run=fake_run)
