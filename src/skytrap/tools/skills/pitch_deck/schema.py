from typing import Literal

from pydantic import BaseModel, Field


class PitchSlide(BaseModel):
    type: Literal["title", "section", "bullets"] = "bullets"
    title: str
    subtitle: str | None = Field(default=None, description="Used by type='title' slides only")
    bullets: list[str] = Field(default_factory=list, description="Used by type='bullets' slides only")


class PitchDeckInput(BaseModel):
    path: str = Field(description="Output path for the .pptx file, relative to the workspace root")
    slides: list[PitchSlide] = Field(min_length=1, description="Ordered slides that make up the deck")
