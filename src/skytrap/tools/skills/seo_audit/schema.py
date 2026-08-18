from pydantic import BaseModel, Field


class SeoAuditInput(BaseModel):
    url: str = Field(
        description="URL of the page to audit — must be http(s)://localhost or http(s)://127.0.0.1"
    )
