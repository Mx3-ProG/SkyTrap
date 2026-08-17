from typing import Literal

from pydantic import BaseModel, Field


class Decision(BaseModel):
    """The model's structured response: either call a tool or give a final answer."""

    type: Literal["tool_call", "final"]
    tool: str | None = None
    arguments: dict = Field(default_factory=dict)
    message: str | None = None
