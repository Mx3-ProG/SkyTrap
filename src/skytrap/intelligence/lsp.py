from __future__ import annotations

import json
import selectors
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

KNOWN_SERVERS: dict[str, tuple[str, ...]] = {
    "python": ("pyright-langserver", "pylsp", "jedi-language-server"),
    "javascript": ("typescript-language-server",), "typescript": ("typescript-language-server",),
    "go": ("gopls",), "rust": ("rust-analyzer",), "c": ("clangd",),
    "cpp": ("clangd",), "java": ("jdtls",),
}


class LspCapabilityResult(BaseModel):
    supported: bool
    detail: str
    data: Any = None


class LanguageIntelligenceProvider:
    """Synchronous LSP JSON-RPC bridge with bounded subprocess lifetime."""

    def __init__(self, detected: dict[str, str] | None = None) -> None:
        self._detected = detected

    def detect(self) -> dict[str, str]:
        if self._detected is not None:
            return self._detected
        detected = {}
        for language, binaries in KNOWN_SERVERS.items():
            for binary in binaries:
                if found := shutil.which(binary):
                    detected[language] = found
                    break
        self._detected = detected
        return detected

    def is_available(self, language: str | None = None) -> bool:
        detected = self.detect()
        return bool(detected) if language is None else language in detected

    def definition(self, workspace=None, path=None, line=0, character=0, language=None, **_):
        return self._text_request("textDocument/definition", workspace, path, language, line, character)

    def references(self, workspace=None, path=None, line=0, character=0, language=None, **_):
        return self._text_request("textDocument/references", workspace, path, language, line, character, {"context": {"includeDeclaration": True}})

    def document_symbols(self, workspace=None, path=None, language=None, **_):
        return self._text_request("textDocument/documentSymbol", workspace, path, language)

    def workspace_symbols(self, workspace=None, query="", language=None, **_):
        return self._request("workspace/symbol", workspace, None, language, {"query": query})

    def diagnostics(self, workspace=None, path=None, language=None, **_):
        return self._text_request("textDocument/diagnostic", workspace, path, language)

    def hover(self, workspace=None, path=None, line=0, character=0, language=None, **_):
        return self._text_request("textDocument/hover", workspace, path, language, line, character)

    def _text_request(self, method, workspace, path, language, line=0, character=0, extra=None):
        if workspace is None or path is None:
            return LspCapabilityResult(supported=False, detail=f"{method} requires workspace and path")
        uri = (Path(workspace.path) / path).resolve().as_uri()
        params = {"textDocument": {"uri": uri}}
        if method in {"textDocument/definition", "textDocument/references", "textDocument/hover"}:
            params["position"] = {"line": line, "character": character}
        params.update(extra or {})
        return self._request(method, workspace, path, language, params)

    def _request(self, method, workspace, path, language, params, timeout=5.0):
        if workspace is None:
            return LspCapabilityResult(supported=False, detail=f"{method} requires workspace")
        language = language or self._language_for(path)
        binary = self.detect().get(language or "")
        if not binary:
            return LspCapabilityResult(supported=False, detail=f"No LSP server detected for {language or 'unknown language'}")
        try:
            process = subprocess.Popen(self._command(binary), cwd=workspace.path, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            assert process.stdin is not None and process.stdout is not None
            self._send(process, 1, "initialize", {"processId": None, "rootUri": workspace.path.resolve().as_uri(), "capabilities": {}})
            self._response(process, 1, timeout)
            self._notify(process, "initialized", {})
            if path:
                absolute = (workspace.path / path).resolve()
                try:
                    text = absolute.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    text = ""
                self._notify(process, "textDocument/didOpen", {"textDocument": {"uri": absolute.as_uri(), "languageId": language, "version": 1, "text": text}})
            self._send(process, 2, method, params)
            response = self._response(process, 2, timeout)
            if "error" in response:
                return LspCapabilityResult(supported=False, detail=str(response["error"]))
            return LspCapabilityResult(supported=True, detail=f"{method} completed via {Path(binary).name}", data=response.get("result"))
        except (OSError, subprocess.SubprocessError, TimeoutError, ValueError, AssertionError) as exc:
            return LspCapabilityResult(supported=False, detail=f"LSP request failed: {exc}")
        finally:
            if "process" in locals():
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()

    @staticmethod
    def _command(binary):
        name = Path(binary).name
        if name in {"typescript-language-server", "pyright-langserver"}:
            return [binary, "--stdio"]
        return [binary, "serve"] if name == "gopls" else [binary]

    @staticmethod
    def _language_for(path):
        return {".py": "python", ".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".rs": "rust", ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".java": "java"}.get(Path(path or "").suffix.lower())

    @staticmethod
    def _write(process, payload):
        raw = json.dumps(payload).encode()
        process.stdin.write(f"Content-Length: {len(raw)}\r\n\r\n".encode() + raw)
        process.stdin.flush()

    def _send(self, process, request_id, method, params):
        self._write(process, {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})

    def _notify(self, process, method, params):
        self._write(process, {"jsonrpc": "2.0", "method": method, "params": params})

    def _response(self, process, request_id, timeout):
        deadline = time.monotonic() + timeout
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            while time.monotonic() < deadline:
                if not selector.select(max(0, deadline - time.monotonic())):
                    break
                headers = {}
                while True:
                    line = process.stdout.readline()
                    if not line:
                        raise ValueError("LSP server closed stdout")
                    if line in {b"\r\n", b"\n"}:
                        break
                    key, value = line.decode(errors="replace").split(":", 1)
                    headers[key.lower()] = value.strip()
                message = json.loads(process.stdout.read(int(headers.get("content-length", "0"))))
                if message.get("id") == request_id:
                    return message
        finally:
            selector.close()
        raise TimeoutError(f"LSP request {request_id} timed out")
