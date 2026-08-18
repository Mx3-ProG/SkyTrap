from pathlib import Path

from pydantic import ValidationError

from skytrap.core.context import WorkspaceContext
from skytrap.tools.base import Tool, ToolResult
from skytrap.tools.filesystem import resolve_in_workspace
from skytrap.tools.registry import RegistryContext, register_tool
from skytrap.tools.skills.contract_review.schema import DEFAULT_CHECKLIST, ContractReviewInput

MAX_EXTRACTED_CHARS = 50_000


def extract_pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def extract_docx_text(path: Path) -> str:
    from docx import Document

    document = Document(str(path))
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


class ContractReviewTool(Tool):
    name = "contract_review"
    description = (
        "Extract the text of a contract (.pdf or .docx) and prepare it for clause-by-clause "
        "review against a checklist. Returns the extracted text plus a checklist — you then "
        "do the actual clause-by-clause comparison yourself using your own reasoning. "
        'Arguments: {"path": "<path to .pdf or .docx>", "checklist": [optional custom items]}'
    )

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        try:
            parsed = ContractReviewInput.model_validate(arguments)
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

        checklist = parsed.checklist or DEFAULT_CHECKLIST
        checklist_text = "\n".join(f"- {item}" for item in checklist)

        output = (
            f"Extracted contract text from {parsed.path}:\n\n{text}\n\n"
            "Review checklist (go through each item against the text above, clause by clause):\n"
            f"{checklist_text}"
        )
        return ToolResult(success=True, output=output)


@register_tool
def _build_contract_review_tool(context: RegistryContext) -> Tool:
    return ContractReviewTool()
