from abc import ABC, abstractmethod


class ModelProvider(ABC):
    """Abstract interface every model backend (local or cloud) must implement."""

    name: str
    engine: str  # "LOCAL", "HYBRID", "CLOUD"

    @abstractmethod
    def chat(self, messages: list[dict]) -> str:
        """Send a chat history (list of {"role", "content"}) and return the model's reply text."""
        raise NotImplementedError

    @property
    def cost_eur(self) -> float:
        """API cost incurred so far. Local providers are always free."""
        return 0.0
