from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, Field

from skytrap.core.context import WorkspaceContext


class ToolResult(BaseModel):
    """Normalized result returned by every executable capability.

    ``success`` and ``output`` remain the small V0.1 compatibility surface.  The
    additional fields let the autonomous runtime reason about failures without
    scraping presentation text and give callers stable observability metadata.
    """

    success: bool
    output: str
    status: Literal["succeeded", "failed", "denied", "needs_approval"] | None = None
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if self.status is None:
            self.status = "succeeded" if self.success else "failed"


class Tool(ABC):
    """A capability SkyTrap can offer the model. The model requests a tool call;
    SkyTrap decides whether and how to execute it — the model never runs code directly.
    """

    name: str
    description: str

    @abstractmethod
    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        raise NotImplementedError
