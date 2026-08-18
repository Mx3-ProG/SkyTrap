from pydantic import BaseModel, Field

# Criteria that justify each triage color — given to the agent alongside the
# extracted text so its RED/YELLOW/GREEN call is grounded in specific, named
# clauses rather than a vague overall impression.
TRIAGE_CRITERIA: dict[str, list[str]] = {
    "RED (do not sign without legal review)": [
        "A non-compete or non-solicit clause is bundled into what should be a plain NDA",
        "Confidentiality obligation is perpetual with no time limit at all",
        "Indemnification is one-sided and/or liability is uncapped",
        "Assignment is allowed without consent, including to a competitor",
        "Governing law / jurisdiction is unusual or clearly disadvantageous to the receiving party",
    ],
    "YELLOW (negotiate before signing)": [
        "Confidentiality term is longer than 3-5 years but not perpetual",
        "The NDA is one-way (non-mutual) when mutual disclosure is actually expected",
        "The definition of 'Confidential Information' is very broad with few carve-outs",
        "No explicit carve-out for independently-developed or already-public information",
    ],
    "GREEN (standard, low-risk)": [
        "Mutual NDA with a standard confidentiality term (roughly 1-3 years)",
        "Standard carve-outs present (public information, independently developed, legally required disclosure)",
        "No non-compete/non-solicit bundled in",
        "Remedies and obligations are reasonable and mutual",
    ],
}


class NdaTriageInput(BaseModel):
    path: str = Field(
        description="Path to the NDA file (.pdf or .docx), relative to the workspace root"
    )
