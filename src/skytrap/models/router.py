from __future__ import annotations

import json
import os

from skytrap.models.base import ModelCapability, ModelProfile, ModelProvider, ModelRole


ROLE_CAPABILITIES = {
    ModelRole.FAST: {ModelCapability.CHAT},
    ModelRole.REASONING: {ModelCapability.CHAT, ModelCapability.REASONING},
    ModelRole.CODING: {ModelCapability.CHAT, ModelCapability.CODE_GENERATION},
    ModelRole.REVIEW: {ModelCapability.CHAT, ModelCapability.REASONING},
    ModelRole.VISION: {ModelCapability.CHAT, ModelCapability.VISION},
}


class ModelRouter:
    """Capability- and qualification-aware provider selection.

    Route preferences are configuration, never business-logic model names. Set
    SKYTRAP_MODEL_ROUTES to JSON such as {"coding":"qwen...","review":"..."}.
    """

    def __init__(
        self,
        providers: list[ModelProvider],
        routes: dict[ModelRole | str, str] | None = None,
        qualification_scores: dict[str, float] | None = None,
    ) -> None:
        self.providers = {provider.name: provider for provider in providers}
        configured = routes if routes is not None else self._routes_from_env()
        self.routes = {ModelRole(role): name for role, name in configured.items()}
        self.qualification_scores = qualification_scores or {}

    @staticmethod
    def _routes_from_env() -> dict[str, str]:
        raw = os.environ.get("SKYTRAP_MODEL_ROUTES", "")
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def route(self, role: ModelRole, *, require_qualified: bool = False) -> ModelProvider:
        required = ROLE_CAPABILITIES[role]
        preferred = self.routes.get(role)
        candidates = [
            provider
            for provider in self.providers.values()
            if provider.capabilities.has(*required)
            and (role in provider.profile.roles or not provider.profile.roles)
        ]
        if require_qualified:
            candidates = [p for p in candidates if self.qualification_scores.get(p.name, 0) >= 0.7]
        if preferred and any(candidate.name == preferred for candidate in candidates):
            return self.providers[preferred]
        if not candidates:
            raise LookupError(f"No configured model satisfies role {role.value}")
        return max(candidates, key=lambda p: self.qualification_scores.get(p.name, 0.0))

    def profiles(self) -> list[ModelProfile]:
        return [provider.profile for provider in self.providers.values()]


def configured_ollama_router(default_provider: ModelProvider) -> "ModelRouter":
    """Build an Ollama router from SKYTRAP_OLLAMA_MODELS and role routes."""
    from skytrap.models.ollama import OllamaProvider

    names = [
        item.strip()
        for item in os.environ.get("SKYTRAP_OLLAMA_MODELS", "").split(",")
        if item.strip()
    ]
    providers = [default_provider]
    providers.extend(OllamaProvider(name) for name in names if name != default_provider.name)
    return ModelRouter(providers)
