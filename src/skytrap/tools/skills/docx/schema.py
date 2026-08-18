from typing import Literal

from pydantic import BaseModel, Field


class DocxBlock(BaseModel):
    type: Literal["heading", "paragraph", "bullet"] = "paragraph"
    text: str
    level: int = Field(default=1, ge=1, le=4, description="Heading level 1-4, ignored for paragraph/bullet")


class DocxGenerateInput(BaseModel):
    path: str = Field(description="Output path for the .docx file, relative to the workspace root")
    blocks: list[DocxBlock] = Field(description="Ordered content blocks that make up the document")
