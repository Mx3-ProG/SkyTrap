from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod

import httpx
from pydantic import BaseModel

from skytrap.technology.hardware import HardwareFit, HardwareProfile


class ModelCandidate(BaseModel):
    name: str
    source: str
    family: str | None = None
    parameter_billions: float | None = None
    required_memory_gb: float | None = None
    hardware_fit: HardwareFit = HardwareFit.BENCHMARK_REQUIRED


class ModelCatalog(ABC):
    @abstractmethod
    def discover(self, hardware: HardwareProfile) -> list[ModelCandidate]:
        raise NotImplementedError


class ConfiguredModelCatalog(ModelCatalog):
    """Open-ended catalogue populated by configuration, not a closed family list."""

    def discover(self, hardware: HardwareProfile) -> list[ModelCandidate]:
        candidates = []
        for name in filter(None, (item.strip() for item in os.environ.get("SKYTRAP_MODEL_CANDIDATES", "").split(","))):
            size = _parameter_size(name)
            required = size * 0.75 if size else None
            candidates.append(ModelCandidate(name=name, source="configured model catalog", family=name.split(":", 1)[0], parameter_billions=size, required_memory_gb=required, hardware_fit=hardware.fit_model(required)))
        return candidates


class OllamaLocalCatalog(ModelCatalog):
    def __init__(self, base_url: str = "http://localhost:11434", client: httpx.Client | None = None) -> None:
        self.base_url = base_url
        self.client = client or httpx.Client(timeout=1.0)

    def discover(self, hardware: HardwareProfile) -> list[ModelCandidate]:
        try:
            response = self.client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            models = response.json().get("models", [])
        except (httpx.HTTPError, ValueError, KeyError):
            return []
        return [
            ModelCandidate(
                name=item["name"],
                source=f"{self.base_url}/api/tags",
                family=item.get("details", {}).get("family"),
                parameter_billions=_parameter_size(item["name"]),
                required_memory_gb=(item.get("size", 0) / 1024**3) or None,
                hardware_fit=hardware.fit_model((item.get("size", 0) / 1024**3) or None),
            )
            for item in models
            if item.get("name")
        ]


class OllamaLibraryCatalog(ModelCatalog):
    """Discovers same-family coder successors from Ollama's official library."""

    def __init__(self, current_model: str, client: httpx.Client | None = None) -> None:
        self.current_model = current_model
        self.client = client or httpx.Client(timeout=2.0, follow_redirects=True)

    def discover(self, hardware: HardwareProfile) -> list[ModelCandidate]:
        family_match = re.match(r"([a-zA-Z_-]+)", self.current_model)
        family = family_match.group(1).rstrip("-_") if family_match else self.current_model.split(":", 1)[0]
        url = f"https://ollama.com/search?q={family}%20coder"
        try:
            response = self.client.get(url)
            response.raise_for_status()
            names = re.findall(r'href=["\']/library/([^"\'/?#]+)', response.text)
        except (httpx.HTTPError, AttributeError):
            return []
        current_generation = _generation(self.current_model)
        unique = []
        for name in names:
            if name in unique or "coder" not in name.lower():
                continue
            if family.lower() not in name.lower() or _generation(name) <= current_generation:
                continue
            unique.append(name)
        return [
            ModelCandidate(
                name=name,
                source=f"https://ollama.com/library/{name}",
                family=family,
                hardware_fit=HardwareFit.BENCHMARK_REQUIRED,
            )
            for name in unique[:10]
        ]


def _parameter_size(name: str) -> float | None:
    match = re.search(r"(?:^|[-:])(\d+(?:\.\d+)?)b(?:$|[-:])", name, re.I)
    return float(match.group(1)) if match else None


def _generation(name: str) -> tuple[int, ...]:
    family = name.split(":", 1)[0]
    numbers = re.findall(r"\d+", family)
    return tuple(int(item) for item in numbers) or (0,)
