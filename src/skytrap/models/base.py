from abc import ABC, abstractmethod


class ModelProvider(ABC):
    """Abstract interface every model backend (local or cloud) must implement."""

    name: str
    engine: str  # "LOCAL", "HYBRID", "CLOUD"

    @abstractmethod
    def generate(self, prompt: str) -> str:
        """Send a prompt to the model and return its full text response."""
        raise NotImplementedError

    @property
    def cost_eur(self) -> float:
        """API cost incurred so far. Local providers are always free."""
        return 0.0
