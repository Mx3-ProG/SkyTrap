import sqlite3
from pathlib import Path

from pydantic import ValidationError

from skytrap.core.context import WorkspaceContext
from skytrap.tools.base import Tool, ToolResult
from skytrap.tools.filesystem import resolve_in_workspace
from skytrap.tools.registry import RegistryContext, register_tool
from skytrap.tools.skills.sql_queries.schema import SqlQueryInput

ALLOWED_EXTENSIONS = {".db", ".sqlite", ".sqlite3"}
ALLOWED_QUERY_PREFIXES = ("SELECT", "WITH", "EXPLAIN")
MAX_ROWS = 200


def _get_schema_text(conn: sqlite3.Connection) -> str:
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    if not tables:
        return "(no tables found)"

    sections = []
    for (table_name,) in tables:
        quoted = table_name.replace('"', '""')
        columns = conn.execute(f'PRAGMA table_info("{quoted}")').fetchall()
        column_lines = "\n".join(f"  - {col[1]} ({col[2]})" for col in columns)
        sections.append(f"{table_name}:\n{column_lines}")
    return "\n\n".join(sections)


def _format_rows(columns: list[str], rows: list[tuple]) -> str:
    if not rows:
        return ("Columns: " + ", ".join(columns) + "\n(no rows)") if columns else "(no rows)"

    header = " | ".join(columns)
    lines = [header, "-" * len(header)]
    lines.extend(" | ".join(str(value) for value in row) for row in rows)
    if len(rows) == MAX_ROWS:
        lines.append(f"... (truncated at {MAX_ROWS} rows)")
    return "\n".join(lines)


class SqlQueryTool(Tool):
    name = "sql_queries"
    description = (
        "Explore the schema of a SQLite database in the workspace, or run a read-only "
        "SELECT/WITH/EXPLAIN query against it (no writes possible — the database is opened "
        "read-only at the driver level regardless of the query text). Use mode='schema' first "
        "to see tables/columns before writing a query. Avoid literal ';' inside string values "
        "in the query — only a single statement is accepted. "
        'Arguments: {"db_path": "<path to .db/.sqlite/.sqlite3>", "mode": "schema"|"query", '
        '"query": "<SQL, required if mode=query>"}'
    )

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        try:
            parsed = SqlQueryInput.model_validate(arguments)
        except ValidationError as exc:
            return ToolResult(success=False, output=f"Invalid arguments: {exc}")

        ok, resolved = resolve_in_workspace(workspace, parsed.db_path)
        if not ok:
            return ToolResult(success=False, output=resolved)

        file_path = Path(resolved)
        if not file_path.exists():
            return ToolResult(success=False, output=f"Database file not found: {parsed.db_path}")
        if file_path.suffix.lower() not in ALLOWED_EXTENSIONS:
            return ToolResult(
                success=False,
                output=f"Unsupported extension: {file_path.suffix!r} (expected .db/.sqlite/.sqlite3)",
            )

        stripped_query = None
        if parsed.mode == "query":
            if not parsed.query:
                return ToolResult(success=False, output="mode='query' requires a 'query' argument")
            stripped_query = parsed.query.strip().rstrip(";").strip()
            if not stripped_query.upper().startswith(ALLOWED_QUERY_PREFIXES):
                return ToolResult(
                    success=False,
                    output="Only read-only SELECT/WITH/EXPLAIN queries are allowed",
                )
            if ";" in stripped_query:
                return ToolResult(
                    success=False, output="Only a single statement is allowed (no ';' inside the query)"
                )

        try:
            # Opened read-only at the SQLite driver level — even if a write somehow
            # got past the prefix check above, the OS-level read-only open makes
            # executing it impossible, not just discouraged.
            conn = sqlite3.connect(f"file:{file_path}?mode=ro", uri=True)
        except sqlite3.OperationalError as exc:
            return ToolResult(success=False, output=f"Could not open database: {exc}")

        try:
            if parsed.mode == "schema":
                output = _get_schema_text(conn)
            else:
                try:
                    cursor = conn.execute(stripped_query)
                except sqlite3.Error as exc:
                    return ToolResult(success=False, output=f"Query failed: {exc}")
                columns = [description[0] for description in cursor.description or []]
                rows = cursor.fetchmany(MAX_ROWS)
                output = _format_rows(columns, rows)
        finally:
            conn.close()

        return ToolResult(success=True, output=output)


@register_tool
def _build_sql_query_tool(context: RegistryContext) -> Tool:
    return SqlQueryTool()
