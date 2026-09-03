from __future__ import annotations

import importlib.metadata
from datetime import datetime, timezone
from enum import StrEnum

import httpx
from pydantic import BaseModel, Field

from skytrap.models.ollama import DEFAULT_MODEL
from skytrap.models.catalog import (
    ConfiguredModelCatalog,
    ModelCatalog,
    OllamaLibraryCatalog,
    OllamaLocalCatalog,
)
from skytrap.technology.hardware import HardwareFit, HardwareProfile


class UpdateCategory(StrEnum):
    MODELS = "models"
    AGENT_RUNTIME = "agent_runtime"
    CODE_INTELLIGENCE = "code_intelligence"
    LSP = "lsp"
    SECURITY = "security"
    TESTING = "testing"
    BROWSER = "browser"
    DEPENDENCIES = "dependencies"


class UpdateStatus(StrEnum):
    CURRENT = "CURRENT"
    UPDATE_AVAILABLE = "UPDATE_AVAILABLE"
    CANDIDATE_MODEL_AVAILABLE = "CANDIDATE_MODEL_AVAILABLE"
    SECURITY_UPDATE = "SECURITY_UPDATE"
    BREAKING_CHANGE_RISK = "BREAKING_CHANGE_RISK"
    BENCHMARK_REQUIRED = "BENCHMARK_REQUIRED"


class UpdateFinding(BaseModel):
    category: UpdateCategory
    technology: str
    source: str
    current_version: str | None = None
    available_version: str | None = None
    release_date: str | None = None
    release_notes_url: str | None = None
    compatibility: str = "unknown"
    security_relevance: str = "unknown"
    recommendation: str
    confidence: float
    hardware_fit: HardwareFit | None = None
    benchmark_required: bool = False
    expected_benefit: str | None = None
    status: UpdateStatus = UpdateStatus.CURRENT


class UpdateReport(BaseModel):
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    hardware: HardwareProfile
    findings: list[UpdateFinding]
    sources_reached: int = 0

    @property
    def benchmark_candidates(self) -> int:
        return sum(item.benchmark_required for item in self.findings)

    @property
    def upgrade_candidates(self) -> int:
        return sum(item.recommendation == "upgrade available" for item in self.findings)


PYPI_TECHNOLOGIES = {
    "typer": UpdateCategory.AGENT_RUNTIME,
    "fastapi": UpdateCategory.AGENT_RUNTIME,
    "tree-sitter": UpdateCategory.CODE_INTELLIGENCE,
    "pyright": UpdateCategory.LSP,
    "pip-audit": UpdateCategory.SECURITY,
    "pytest": UpdateCategory.TESTING,
    "playwright": UpdateCategory.BROWSER,
    "pydantic": UpdateCategory.DEPENDENCIES,
}


class TechnologyWatch:
    """Read-only freshness scanner backed exclusively by official registries."""

    def __init__(self, client: httpx.Client | None = None, catalogs: list[ModelCatalog] | None = None) -> None:
        self.client = client or httpx.Client(timeout=2.0, follow_redirects=True)
        self.catalogs = catalogs or [
            ConfiguredModelCatalog(),
            OllamaLocalCatalog(),
            OllamaLibraryCatalog(DEFAULT_MODEL),
        ]

    def check(self) -> UpdateReport:
        hardware = HardwareProfile.detect()
        findings = [self._model_finding(hardware, self.catalogs)]
        reached = 0
        for package, category in PYPI_TECHNOLOGIES.items():
            finding, source_ok = self._pypi_finding(package, category)
            findings.append(finding)
            reached += int(source_ok)
        return UpdateReport(hardware=hardware, findings=findings, sources_reached=reached)

    def _pypi_finding(self, package: str, category: UpdateCategory) -> tuple[UpdateFinding, bool]:
        try:
            current = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            current = None
        url = f"https://pypi.org/pypi/{package}/json"
        try:
            response = self.client.get(url)
            response.raise_for_status()
            info = response.json()["info"]
            available = info["version"]
            recommendation = (
                "optional tool available"
                if current is None
                else "keep"
                if current == available
                else "upgrade available"
            )
            status = UpdateStatus.CURRENT
            if current is None or current != available:
                status = UpdateStatus.SECURITY_UPDATE if current is not None and category == UpdateCategory.SECURITY else UpdateStatus.UPDATE_AVAILABLE
            if current and current != available:
                try:
                    if int(available.split(".", 1)[0]) > int(current.split(".", 1)[0]):
                        status = UpdateStatus.BREAKING_CHANGE_RISK
                except ValueError:
                    pass
            releases = response.json().get("releases", {}).get(available, [])
            release_date = releases[0].get("upload_time_iso_8601") if releases else None
            return UpdateFinding(category=category, technology=package, source=url, current_version=current, available_version=available, release_date=release_date, release_notes_url=info.get("project_url") or info.get("package_url"), compatibility="inspect release notes before isolated upgrade", security_relevance="check official advisories before upgrading", recommendation=recommendation, confidence=0.95, status=status), True
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            return UpdateFinding(category=category, technology=package, source=url, current_version=current, recommendation="source unavailable; no upgrade inference made", confidence=0.0, compatibility="unknown", security_relevance="unknown"), False

    @staticmethod
    def _model_finding(hardware: HardwareProfile, catalogs: list[ModelCatalog]) -> UpdateFinding:
        candidates = []
        for catalog in catalogs:
            candidates.extend(catalog.discover(hardware))
        candidates = [item for item in candidates if item.name != DEFAULT_MODEL]
        candidate = candidates[0] if candidates else None
        available = candidate.name if candidate else None
        return UpdateFinding(
            category=UpdateCategory.MODELS,
            technology=DEFAULT_MODEL,
            source=candidate.source if candidate else "Ollama local registry / configured catalog",
            current_version=DEFAULT_MODEL,
            available_version=available,
            compatibility="qualification required" if available else "no trusted candidate configured",
            security_relevance="model behavior must be benchmarked",
            recommendation="benchmark candidate" if available else "keep; no verified newer candidate",
            confidence=0.9 if available else 0.6,
            hardware_fit=candidate.hardware_fit if candidate else None,
            benchmark_required=bool(available),
            expected_benefit=(
                "newer same-family coding generation; improvement is unproven until qualification"
                if available
                else None
            ),
            status=UpdateStatus.BENCHMARK_REQUIRED if available else UpdateStatus.CURRENT,
        )
