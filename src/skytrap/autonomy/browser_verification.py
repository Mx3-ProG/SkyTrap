from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel, Field

from skytrap.core.context import WorkspaceContext
from skytrap.intelligence.snapshot import RepositorySnapshot


class BrowserVerificationResult(BaseModel):
    success: bool
    url: str
    status_code: int | None = None
    console_errors: list[str] = Field(default_factory=list)
    screenshot: str | None = None
    detail: str = ""
    skipped: bool = False
    verification_level: str = "none"
    title: str | None = None
    dom_visible: bool = False
    interaction_passed: bool = False


class BrowserCapabilityStatus(StrEnum):
    HTTP_ONLY = "HTTP_ONLY"
    PLAYWRIGHT_LIBRARY_AVAILABLE = "PLAYWRIGHT_LIBRARY_AVAILABLE"
    BROWSER_MISSING = "BROWSER_MISSING"
    FULL_BROWSER_VERIFICATION = "FULL_BROWSER_VERIFICATION"


class BrowserCapabilityReport(BaseModel):
    status: BrowserCapabilityStatus
    python_library: bool = False
    node_library: bool = False
    chromium_installed: bool = False
    launch_working: bool = False
    screenshot_working: bool = False
    interaction_working: bool = False
    detail: str


class BrowserVerificationProvider:
    """Optional real-browser verification with HTTP fallback.

    It only starts a discovered dev server when its executable dependencies are
    already installed; it never performs an implicit package installation.
    """

    def available(self) -> bool:
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            return False
        return True

    def probe(self) -> BrowserCapabilityReport:
        python_library = self.available()
        node_library = False
        if shutil.which("node"):
            try:
                node_library = subprocess.run(
                    ["node", "-e", "require.resolve('playwright')"],
                    capture_output=True,
                    timeout=3,
                ).returncode == 0
            except (OSError, subprocess.SubprocessError):
                pass
        if not python_library:
            return BrowserCapabilityReport(
                status=(BrowserCapabilityStatus.PLAYWRIGHT_LIBRARY_AVAILABLE if node_library else BrowserCapabilityStatus.HTTP_ONLY),
                node_library=node_library,
                detail=("Node Playwright library found; SkyTrap's Python runtime cannot drive it." if node_library else "Playwright library is not available; HTTP checks remain available."),
            )
        try:
            from playwright.sync_api import sync_playwright

            with TemporaryDirectory(prefix="skytrap-browser-probe-") as raw:
                screenshot = Path(raw) / "probe.png"
                with sync_playwright() as playwright:
                    executable = Path(playwright.chromium.executable_path)
                    if not executable.exists():
                        return BrowserCapabilityReport(
                            status=BrowserCapabilityStatus.BROWSER_MISSING,
                            python_library=True,
                            node_library=node_library,
                            detail="Playwright is importable, but its Chromium executable is missing.",
                        )
                    browser = playwright.chromium.launch(headless=True)
                    page = browser.new_page()
                    page.set_content("<title>SkyTrap probe</title><input id='q'><button onclick=\"document.querySelector('#q').value='rabbit'\">Go</button><main>Rabbit</main>")
                    page.click("button")
                    interaction = page.input_value("#q") == "rabbit"
                    visible = page.locator("main").is_visible()
                    page.screenshot(path=str(screenshot))
                    browser.close()
                screenshot_ok = screenshot.exists() and screenshot.stat().st_size > 0
            return BrowserCapabilityReport(
                status=(BrowserCapabilityStatus.FULL_BROWSER_VERIFICATION if visible and interaction and screenshot_ok else BrowserCapabilityStatus.PLAYWRIGHT_LIBRARY_AVAILABLE),
                python_library=True,
                node_library=node_library,
                chromium_installed=True,
                launch_working=True,
                screenshot_working=screenshot_ok,
                interaction_working=interaction,
                detail="Chromium launch, DOM visibility, screenshot and input/button interaction passed." if visible and interaction and screenshot_ok else "Playwright launched, but its functional browser probe was incomplete.",
            )
        except Exception as exc:  # noqa: BLE001
            return BrowserCapabilityReport(
                status=BrowserCapabilityStatus.BROWSER_MISSING,
                python_library=True,
                node_library=node_library,
                detail=f"Playwright is importable, but Chromium could not launch: {exc}",
            )

    def discover_server_command(self, workspace: WorkspaceContext, snapshot: RepositorySnapshot) -> list[str] | None:
        package = workspace.path / "package.json"
        node_modules = workspace.path / "node_modules"
        if package.exists() and node_modules.is_dir() and shutil.which("npm"):
            try:
                scripts = json.loads(package.read_text()).get("scripts", {})
            except (OSError, ValueError):
                scripts = {}
            for name in ("dev", "start", "serve"):
                if name in scripts:
                    return ["npm", "run", name, "--", "--host", "127.0.0.1"]
        return None

    def verify(
        self,
        workspace: WorkspaceContext,
        snapshot: RepositorySnapshot,
        *,
        url: str | None = None,
        server_command: list[str] | None = None,
        timeout: float = 15.0,
        screenshot: Path | None = None,
    ) -> BrowserVerificationResult:
        url = url or os.environ.get("SKYTRAP_BROWSER_VERIFY_URL") or "http://127.0.0.1:5173"
        command = server_command or self.discover_server_command(workspace, snapshot)
        process = None
        if command:
            process = subprocess.Popen(command, cwd=workspace.path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif not os.environ.get("SKYTRAP_BROWSER_VERIFY_URL"):
            return BrowserVerificationResult(success=False, url=url, skipped=True, detail="No installed runnable web server was discovered.")
        try:
            status = self._wait_http(url, timeout)
            if status is None:
                return BrowserVerificationResult(success=False, url=url, detail="Server did not become reachable before timeout.")
            if not self.available():
                return BrowserVerificationResult(success=200 <= status < 400, url=url, status_code=status, verification_level="http", detail="HTTP navigation passed; browser verification was not performed because Playwright is unavailable.")
            try:
                from playwright.sync_api import sync_playwright

                errors: list[str] = []
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    page = browser.new_page()
                    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
                    response = page.goto(url, wait_until="networkidle", timeout=int(timeout * 1000))
                    title = page.title()
                    dom_visible = page.locator("body").is_visible()
                    if screenshot:
                        page.screenshot(path=str(screenshot), full_page=True)
                    browser.close()
            except Exception as exc:  # noqa: BLE001 - browser/runtime failures are evidence
                return BrowserVerificationResult(success=False, url=url, status_code=status, detail=f"Playwright verification failed: {exc}")
            code = response.status if response else status
            return BrowserVerificationResult(success=200 <= code < 400 and not errors and dom_visible, url=url, status_code=code, console_errors=errors, screenshot=str(screenshot) if screenshot else None, verification_level="browser", title=title, dom_visible=dom_visible, detail="Real Playwright navigation, visible DOM and console inspection completed.")
        finally:
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()

    @staticmethod
    def _wait_http(url: str, timeout: float) -> int | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    return response.status
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.2)
        return None
