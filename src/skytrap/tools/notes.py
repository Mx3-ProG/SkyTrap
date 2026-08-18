from skytrap.core.context import WorkspaceContext
from skytrap.memory.sqlite import SqliteMemory
from skytrap.tools.base import Tool, ToolResult


class GetPastNotesTool(Tool):
    name = "get_past_notes"
    description = (
        "Retrieve SkyTrap's own past work-log notes for this workspace — short "
        "summaries of previous sessions, to reorient before starting new work. "
        'Arguments: {"mode": "recent" or "search", "query": "<text, required if mode=search>", '
        '"limit": <optional int, default 10>}'
    )

    def __init__(self, memory: SqliteMemory):
        self._memory = memory

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        mode = arguments.get("mode", "recent")
        limit = int(arguments.get("limit", 10))
        workspace_path = str(workspace.path)

        if mode == "search":
            query = arguments.get("query")
            if not query:
                return ToolResult(success=False, output="mode=search requires a 'query' argument")
            notes = self._memory.search_notes(workspace_path, query, limit=limit)
        elif mode == "recent":
            notes = self._memory.list_notes(workspace_path, limit=limit)
        else:
            return ToolResult(success=False, output=f"Unknown mode: {mode!r} (use 'recent' or 'search')")

        if not notes:
            return ToolResult(success=True, output="No past notes found for this workspace.")

        lines = [f"[{note.created_at}] {note.summary}" for note in notes]
        return ToolResult(success=True, output="\n\n".join(lines))
