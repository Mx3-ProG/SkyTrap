import httpx

from skytrap.models.base import ModelProvider

DEFAULT_MODEL = "qwen2.5-coder:7b"
DEFAULT_BASE_URL = "http://localhost:11434"


class OllamaProvider(ModelProvider):
    """Runs an open-weight coding model locally via Ollama."""

    engine = "LOCAL"

    def __init__(self, model: str = DEFAULT_MODEL, base_url: str = DEFAULT_BASE_URL):
        self.name = model
        self.base_url = base_url

    def generate(self, prompt: str) -> str:
        response = httpx.post(
            f"{self.base_url}/api/generate",
            json={"model": self.name, "prompt": prompt, "stream": False},
            timeout=120.0,
        )
        response.raise_for_status()
        return response.json()["response"]
