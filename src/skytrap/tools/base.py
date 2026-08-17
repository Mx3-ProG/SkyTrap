from abc import ABC, abstractmethod

from pydantic import BaseModel

from skytrap.core.context import WorkspaceContext


class ToolResult(BaseModel):
    success: bool
    output: str


class Tool(ABC):
    """A capability SkyTrap can offer the model. The model requests a tool call;
    SkyTrap decides whether and how to execute it — the model never runs code directly.
    """

    name: str
    description: str

    @abstractmethod
    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        raise NotImplementedError
