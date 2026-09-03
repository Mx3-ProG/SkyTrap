import shutil
from enum import StrEnum

import httpx
from pydantic import BaseModel, Field

from skytrap.models.base import (
    ModelCapabilities,
    ModelCapability,
    ModelProfile,
    ModelProvider,
    ModelRole,
)

DEFAULT_MODEL = "qwen2.5-coder:7b"
DEFAULT_BASE_URL = "http://localhost:11434"


class OllamaHealthStatus(StrEnum):
    NOT_INSTALLED = "NOT_INSTALLED"
    DAEMON_OFFLINE = "DAEMON_OFFLINE"
    MODEL_MISSING = "MODEL_MISSING"
    MODEL_UNLOADABLE = "MODEL_UNLOADABLE"
    HEALTHY = "HEALTHY"


class OllamaHealthReport(BaseModel):
    status: OllamaHealthStatus
    model: str
    binary_present: bool = False
    daemon_accessible: bool = False
    api_accessible: bool = False
    model_present: bool = False
    model_loadable: bool = False
    generation_working: bool = False
    version: str | None = None
    detail: str = ""
    recommendations: list[str] = Field(default_factory=list)


def probe_ollama(
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    *,
    timeout: float = 2.0,
    generate: bool = True,
) -> OllamaHealthReport:
    """Probe every Ollama layer without installing, starting, or pulling anything."""
    binary = shutil.which("ollama")
    if not binary:
        return OllamaHealthReport(
            status=OllamaHealthStatus.NOT_INSTALLED,
            model=model,
            detail="Ollama binary is not on PATH.",
            recommendations=["Install Ollama, then run `ollama serve` and `ollama pull %s`." % model],
        )
    try:
        version_response = httpx.get(f"{base_url}/api/version", timeout=timeout)
        version_response.raise_for_status()
        tags_response = httpx.get(f"{base_url}/api/tags", timeout=timeout)
        tags_response.raise_for_status()
        payload = tags_response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return OllamaHealthReport(
            status=OllamaHealthStatus.DAEMON_OFFLINE,
            model=model,
            binary_present=True,
            detail=f"Ollama binary found at {binary}, but its local API is unreachable: {exc}",
            recommendations=["Start the daemon with `ollama serve`."],
        )
    version = None
    try:
        version = version_response.json().get("version")
    except ValueError:
        pass
    names = {item.get("name") for item in payload.get("models", []) if isinstance(item, dict)}
    names |= {item.get("model") for item in payload.get("models", []) if isinstance(item, dict)}
    present = model in names or (":" not in model and f"{model}:latest" in names)
    if not present:
        return OllamaHealthReport(
            status=OllamaHealthStatus.MODEL_MISSING,
            model=model,
            binary_present=True,
            daemon_accessible=True,
            api_accessible=True,
            version=version,
            detail=f"Ollama API is healthy, but configured model `{model}` is absent.",
            recommendations=[f"Pull the configured model with `ollama pull {model}`."],
        )
    if not generate:
        return OllamaHealthReport(
            status=OllamaHealthStatus.HEALTHY,
            model=model,
            binary_present=True,
            daemon_accessible=True,
            api_accessible=True,
            model_present=True,
            version=version,
            detail="Model is installed; generation was not requested.",
        )
    try:
        response = httpx.post(
            f"{base_url}/api/generate",
            json={"model": model, "prompt": "Reply with OK", "stream": False, "options": {"num_predict": 4}},
            timeout=max(timeout, 30.0),
        )
        response.raise_for_status()
        generated = response.json().get("response")
        if not isinstance(generated, str) or not generated.strip():
            raise ValueError("generation returned an empty response")
    except (httpx.HTTPError, ValueError) as exc:
        return OllamaHealthReport(
            status=OllamaHealthStatus.MODEL_UNLOADABLE,
            model=model,
            binary_present=True,
            daemon_accessible=True,
            api_accessible=True,
            model_present=True,
            version=version,
            detail=f"Configured model is installed but a minimal generation failed: {exc}",
            recommendations=[f"Run `ollama run {model} \"Reply with OK\"` and inspect Ollama logs."],
        )
    return OllamaHealthReport(
        status=OllamaHealthStatus.HEALTHY,
        model=model,
        binary_present=True,
        daemon_accessible=True,
        api_accessible=True,
        model_present=True,
        model_loadable=True,
        generation_working=True,
        version=version,
        detail="Binary, daemon, API, configured model, load and minimal generation all passed.",
    )


class OllamaProvider(ModelProvider):
    """Runs an open-weight coding model locally via Ollama."""

    engine = "LOCAL"

    def __init__(self, model: str = DEFAULT_MODEL, base_url: str = DEFAULT_BASE_URL):
        self.name = model
        self.base_url = base_url

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            supported={
                ModelCapability.CHAT,
                ModelCapability.STRUCTURED_OUTPUT,
                ModelCapability.TOOL_CALLING,
                ModelCapability.CODE_GENERATION,
                ModelCapability.REASONING,
            },
            context_window=32768,
        )

    @property
    def profile(self) -> ModelProfile:
        return ModelProfile(
            name=self.name,
            provider="ollama",
            capabilities=self.capabilities,
            roles={ModelRole.FAST, ModelRole.REASONING, ModelRole.CODING, ModelRole.REVIEW},
        )

    def chat(self, messages: list[dict]) -> str:
        response = httpx.post(
            f"{self.base_url}/api/chat",
            json={"model": self.name, "messages": messages, "stream": False},
            timeout=120.0,
        )
        response.raise_for_status()
        return response.json()["message"]["content"]

    def is_available(self, timeout: float = 0.5) -> bool:
        """Fast health probe used by the startup dashboard; never raises offline."""
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=timeout)
            response.raise_for_status()
        except httpx.HTTPError:
            return False
        return True
