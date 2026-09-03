from abc import ABC, abstractmethod
from enum import StrEnum

from pydantic import BaseModel, Field


class ModelCapability(StrEnum):
    CHAT = "chat"
    STRUCTURED_OUTPUT = "structured_output"
    TOOL_CALLING = "tool_calling"
    STREAMING = "streaming"
    LONG_CONTEXT = "long_context"
    CODE_GENERATION = "code_generation"
    REASONING = "reasoning"
    VISION = "vision"


class ModelRole(StrEnum):
    FAST = "fast"
    REASONING = "reasoning"
    CODING = "coding"
    REVIEW = "review"
    VISION = "vision"


class ModelCapabilities(BaseModel):
    supported: set[ModelCapability] = Field(default_factory=lambda: {ModelCapability.CHAT})
    context_window: int = 4096

    def has(self, *capabilities: ModelCapability) -> bool:
        return all(capability in self.supported for capability in capabilities)


class ModelProfile(BaseModel):
    name: str
    provider: str
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    roles: set[ModelRole] = Field(default_factory=set)
    memory_gb: float | None = None
    qualified: bool = False


class ModelProvider(ABC):
    """Abstract interface every model backend (local or cloud) must implement."""

    name: str
    engine: str  # "LOCAL", "HYBRID", "CLOUD"

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities()

    @property
    def profile(self) -> ModelProfile:
        return ModelProfile(
            name=self.name,
            provider=self.engine.lower(),
            capabilities=self.capabilities,
        )

    @abstractmethod
    def chat(self, messages: list[dict]) -> str:
        """Send a chat history (list of {"role", "content"}) and return the model's reply text."""
        raise NotImplementedError

    @property
    def cost_eur(self) -> float:
        """API cost incurred so far. Local providers are always free."""
        return 0.0
