from skytrap.core.context import WorkspaceContext
from skytrap.intelligence.structural_search import StructuralSearch
from skytrap.tools.base import Tool, ToolResult


class StructuralSearchTool(Tool):
    """Item 6/7 — structural code search exposed to the model as a real, read-only
    tool (never raw shell access to ast-grep/tree-sitter). Any change the model
    decides to make based on what it finds here still goes through write_file/
    patch_file/delete_file — this tool cannot itself modify anything."""

    name = "search_structure"
    description = (
        "Search the codebase structurally rather than by plain text: find calls to a "
        "function, a declaration, or a small ast-grep pattern (e.g. 'functionName($$$ARGS)'). "
        "Prefer this over search_code when you need to find every call site of a specific "
        "function/symbol, not just a text match. Uses ast-grep when installed, otherwise an "
        "approximate tree-sitter + ripgrep fallback (results are marked 'approximate' in that case). "
        'Arguments: {"pattern": "<ast-grep pattern or bare identifier>", "language": "<optional>"}'
    )

    def __init__(self, search: StructuralSearch | None = None):
        self._search = search or StructuralSearch()

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        pattern = arguments.get("pattern")
        if not pattern:
            return ToolResult(success=False, output="Missing required argument 'pattern'")
        language = arguments.get("language")
        matches = self._search.search(workspace, pattern, language=language)
        if not matches:
            return ToolResult(success=True, output="No structural matches found.", metadata={"backend": self._search.backend()})
        lines = [f"{m.file}:{m.line}: {m.text}" + (" (approximate)" if m.approximate else "") for m in matches[:50]]
        return ToolResult(
            success=True,
            output="\n".join(lines),
            metadata={"backend": self._search.backend(), "match_count": len(matches)},
        )
