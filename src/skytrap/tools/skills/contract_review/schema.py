from pydantic import BaseModel, Field

DEFAULT_CHECKLIST = [
    "Termination: notice period, for-cause vs for-convenience",
    "Liability cap: is there one, and is it reasonable relative to contract value",
    "Indemnification: scope, mutuality",
    "IP ownership: who owns the work product",
    "Confidentiality: duration, survival after termination",
    "Governing law / jurisdiction",
    "Auto-renewal: notice period required to opt out",
    "Payment terms: due dates, late fees",
    "Assignment: can either party assign without the other's consent",
    "Non-compete / non-solicit: scope and duration",
]


class ContractReviewInput(BaseModel):
    path: str = Field(
        description="Path to the contract file (.pdf or .docx), relative to the workspace root"
    )
    checklist: list[str] | None = Field(
        default=None,
        description="Custom checklist items; defaults to a standard commercial-contract checklist if omitted",
    )
