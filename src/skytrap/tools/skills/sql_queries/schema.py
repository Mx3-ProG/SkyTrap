from typing import Literal

from pydantic import BaseModel, Field


class SqlQueryInput(BaseModel):
    db_path: str = Field(
        description="Path to a SQLite database file (.db/.sqlite/.sqlite3), relative to the workspace root"
    )
    mode: Literal["schema", "query"] = Field(
        default="schema",
        description="'schema' lists tables/columns; 'query' runs a read-only SELECT/WITH/EXPLAIN statement",
    )
    query: str | None = Field(
        default=None, description="SQL statement to run; required when mode='query'"
    )
