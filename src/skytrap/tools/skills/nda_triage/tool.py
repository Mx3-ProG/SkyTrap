from pathlib import Path

from pydantic import ValidationError

from skytrap.core.context import WorkspaceContext
from skytrap.tools.base import Tool, ToolResult
from skytrap.tools.filesystem import resolve_in_workspace
from skytrap.tools.registry import RegistryContext, register_tool
from skytrap.tools.skills.nda_triage.schema import TRIAGE_CRITERIA, NdaTriageInput

MAX_EXTRACTED_CHARS = 50_000


# Deliberately not shared with tools/skills/contract_review — each skill is isolated
# and self-contained per the project's rule (no skill depends on another skill), even
# though the extraction logic is nearly identical. The duplication is small (~10 lines).
def extract_pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def extract_docx_text(path: Path) -> str:
    from docx import Document

    document = Document(str(path))
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


class NdaTriageTool(Tool):
    name = "nda_triage"
    description = (
        "Extract the text of an NDA (.pdf or .docx) and pair it with RED/YELLOW/GREEN triage "
        "criteria, so you can classify it and justify the classification by citing specific "
        "clauses. This tool does not decide the classification itself — you do, using the "
        'criteria it returns. Arguments: {"path": "<path to .pdf or .docx>"}'
    )

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        try:
            parsed = NdaTriageInput.model_validate(arguments)
        except ValidationError as exc:
            return ToolResult(success=False, output=f"Invalid arguments: {exc}")

        ok, resolved = resolve_in_workspace(workspace, parsed.path)
        if not ok:
            return ToolResult(success=False, output=resolved)

        file_path = Path(resolved)
        if not file_path.exists():
            return ToolResult(success=False, output=f"File not found: {parsed.path}")

        suffix = file_path.suffix.lower()
        try:
            if suffix == ".pdf":
                text = extract_pdf_text(file_path)
            elif suffix == ".docx":
                text = extract_docx_text(file_path)
            else:
                return ToolResult(
                    success=False,
                    output=f"Unsupported file type: {suffix!r} (only .pdf and .docx are supported)",
                )
        except Exception as exc:  # noqa: BLE001 - surface any extraction failure as a tool error
            return ToolResult(success=False, output=f"Failed to extract text from {parsed.path}: {exc}")

        text = text.strip()
        if not text:
            return ToolResult(success=False, output=f"No extractable text found in {parsed.path}")
        if len(text) > MAX_EXTRACTED_CHARS:
            text = text[:MAX_EXTRACTED_CHARS] + "\n... (truncated)"

        criteria_text = "\n\n".join(
            f"{label}:\n" + "\n".join(f"- {item}" for item in items)
            for label, items in TRIAGE_CRITERIA.items()
        )

        output = (
            f"Extracted NDA text from {parsed.path}:\n\n{text}\n\n"
            "Triage criteria — classify this NDA as RED, YELLOW, or GREEN, citing the "
            "specific clause(s) in the text above that justify your call:\n\n"
            f"{criteria_text}"
        )
        return ToolResult(success=True, output=output)


@register_tool
def _build_nda_triage_tool(context: RegistryContext) -> Tool:
    return NdaTriageTool()
