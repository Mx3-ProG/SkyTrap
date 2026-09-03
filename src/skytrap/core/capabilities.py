from __future__ import annotations

from enum import StrEnum
import shutil

from pydantic import BaseModel, Field


class CapabilityHealth(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    DETECTED = "DETECTED"
    DEGRADED = "DEGRADED"
    HEALTHY = "HEALTHY"


class CapabilityRecord(BaseModel):
    name: str
    health: CapabilityHealth
    detail: str
    fallback: str | None = None

    @property
    def usable(self) -> bool:
        return self.health in {CapabilityHealth.DEGRADED, CapabilityHealth.HEALTHY}


class CapabilityMatrix(BaseModel):
    capabilities: dict[str, CapabilityRecord] = Field(default_factory=dict)

    def record(self, name: str, health: CapabilityHealth, detail: str, fallback: str | None = None) -> None:
        self.capabilities[name] = CapabilityRecord(name=name, health=health, detail=detail, fallback=fallback)

    def get(self, name: str) -> CapabilityRecord:
        return self.capabilities.get(
            name,
            CapabilityRecord(name=name, health=CapabilityHealth.UNAVAILABLE, detail="not probed"),
        )

    def planner_prompt(self) -> str:
        lines = ["Runtime capability matrix (never plan an unavailable capability without its fallback):"]
        for name, capability in sorted(self.capabilities.items()):
            line = f"- {name}: {capability.health.value} — {capability.detail}"
            if capability.fallback:
                line += f"; fallback: {capability.fallback}"
            lines.append(line)
        return "\n".join(lines)


def detect_runtime_capabilities() -> CapabilityMatrix:
    """Cheap, side-effect-free matrix for planning; `doctor` performs deeper probes."""
    matrix = CapabilityMatrix()
    matrix.record("model_reasoning", CapabilityHealth.DETECTED, "configured provider; qualification is reported separately")
    matrix.record("text_search", CapabilityHealth.HEALTHY if shutil.which("rg") else CapabilityHealth.DEGRADED, "ripgrep on PATH" if shutil.which("rg") else "ripgrep missing", "bounded Python text scan")
    try:
        from skytrap.intelligence.parser import CodeParser
        parser_ok = bool(CodeParser().supported_languages())
    except Exception:  # noqa: BLE001
        parser_ok = False
    matrix.record("ast_parsing", CapabilityHealth.HEALTHY if parser_ok else CapabilityHealth.UNAVAILABLE, "Tree-sitter grammar probe passed" if parser_ok else "no usable Tree-sitter grammar", "text search")
    ast_grep = shutil.which("ast-grep") or shutil.which("sg")
    matrix.record("structural_search", CapabilityHealth.DETECTED if ast_grep else CapabilityHealth.DEGRADED, ast_grep or "ast-grep missing", "Tree-sitter + ripgrep approximate search")
    try:
        from skytrap.autonomy.browser_verification import BrowserVerificationProvider
        browser_available = BrowserVerificationProvider().available()
    except Exception:  # noqa: BLE001
        browser_available = False
    matrix.record("browser_verification", CapabilityHealth.DETECTED if browser_available else CapabilityHealth.DEGRADED, "Playwright library detected; run doctor for launch probe" if browser_available else "HTTP_ONLY", "HTTP reachability explicitly labelled HTTP_ONLY")
    matrix.record("git", CapabilityHealth.HEALTHY if shutil.which("git") else CapabilityHealth.UNAVAILABLE, "git on PATH" if shutil.which("git") else "git missing", "no branch/checkpoint/rollback")
    return matrix
