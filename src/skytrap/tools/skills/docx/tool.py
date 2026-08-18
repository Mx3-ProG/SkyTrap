from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from skytrap.core.context import WorkspaceContext
from skytrap.tools.base import Tool, ToolResult
from skytrap.tools.filesystem import resolve_in_workspace
from skytrap.tools.registry import RegistryContext, register_tool
from skytrap.tools.skills.docx.schema import DocxBlock, DocxGenerateInput


def _build_preview(path_arg: str, blocks: list[DocxBlock]) -> str:
    lines = [f"NEW DOCX FILE: {path_arg}", ""]
    for block in blocks:
        if block.type == "heading":
            lines.append(f"{'#' * block.level} {block.text}")
        elif block.type == "bullet":
            lines.append(f"- {block.text}")
        else:
            lines.append(block.text)
        lines.append("")
    return "\n".join(lines).rstrip()


class DocxGenerateTool(Tool):
    name = "docx_generate"
    description = (
        "Generate a Word (.docx) document from structured content blocks. Shows the user "
        "a text preview of the content and requires confirmation before writing anything, "
        "same as write_file — this tool never writes silently. "
        'Arguments: {"path": "<output path ending in .docx>", "blocks": [{"type": '
        '"heading"|"paragraph"|"bullet", "text": "...", "level": 1-4 (heading only)}]}'
    )

    def __init__(self, confirm: Callable[[str], bool]):
        self._confirm = confirm

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        try:
            parsed = DocxGenerateInput.model_validate(arguments)
        except ValidationError as exc:
            return ToolResult(success=False, output=f"Invalid arguments: {exc}")

        if not parsed.path.lower().endswith(".docx"):
            return ToolResult(success=False, output="Output path must end in .docx")
        if not parsed.blocks:
            return ToolResult(success=False, output="'blocks' must contain at least one content block")

        ok, resolved = resolve_in_workspace(workspace, parsed.path)
        if not ok:
            return ToolResult(success=False, output=resolved)

        file_path = Path(resolved)
        preview = _build_preview(parsed.path, parsed.blocks)
        if not self._confirm(preview):
            return ToolResult(success=False, output="User declined the write; file not created.")

        from docx import Document

        document = Document()
        for block in parsed.blocks:
            if block.type == "heading":
                document.add_heading(block.text, level=block.level)
            elif block.type == "bullet":
                document.add_paragraph(block.text, style="List Bullet")
            else:
                document.add_paragraph(block.text)

        file_path.parent.mkdir(parents=True, exist_ok=True)
        document.save(str(file_path))

        return ToolResult(success=True, output=f"Wrote {len(parsed.blocks)} block(s) to {parsed.path}")


@register_tool
def _build_docx_generate_tool(context: RegistryContext) -> Tool:
    return DocxGenerateTool(confirm=context.confirm_write)
