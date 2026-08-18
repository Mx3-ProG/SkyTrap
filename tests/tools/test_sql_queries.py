import sqlite3
from pathlib import Path

from skytrap.core.context import WorkspaceContext
from skytrap.tools.skills.sql_queries.tool import SqlQueryTool


def _workspace(tmp_path: Path) -> WorkspaceContext:
    return WorkspaceContext(path=tmp_path.resolve(), name=tmp_path.name, is_git=False)


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT)")
    conn.execute("INSERT INTO users (name, email) VALUES ('Alice', 'alice@example.com')")
    conn.execute("INSERT INTO users (name, email) VALUES ('Bob', 'bob@example.com')")
    conn.commit()
    conn.close()


def test_schema_mode_lists_table_and_columns(tmp_path):
    _make_db(tmp_path / "app.sqlite")

    result = SqlQueryTool().execute(_workspace(tmp_path), {"db_path": "app.sqlite", "mode": "schema"})

    assert result.success
    assert "users:" in result.output
    assert "name (TEXT)" in result.output
    assert "email (TEXT)" in result.output


def test_query_mode_returns_real_rows(tmp_path):
    _make_db(tmp_path / "app.sqlite")

    result = SqlQueryTool().execute(
        _workspace(tmp_path),
        {"db_path": "app.sqlite", "mode": "query", "query": "SELECT name, email FROM users ORDER BY name"},
    )

    assert result.success
    assert "Alice" in result.output
    assert "alice@example.com" in result.output
    assert "Bob" in result.output


def test_query_mode_rejects_write_statements(tmp_path):
    _make_db(tmp_path / "app.sqlite")

    result = SqlQueryTool().execute(
        _workspace(tmp_path),
        {"db_path": "app.sqlite", "mode": "query", "query": "DELETE FROM users"},
    )

    assert not result.success
    assert "read-only" in result.output.lower()

    # verify nothing was actually deleted
    conn = sqlite3.connect(tmp_path / "app.sqlite")
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    assert count == 2


def test_query_mode_write_blocked_even_if_prefix_check_is_bypassed(tmp_path):
    """Defense in depth: even a statement crafted to start with an allowed prefix
    can't actually mutate the database, because the connection itself is read-only
    at the SQLite driver level."""
    _make_db(tmp_path / "app.sqlite")
    tool = SqlQueryTool()

    result = tool.execute(
        _workspace(tmp_path),
        {
            "db_path": "app.sqlite",
            "mode": "query",
            "query": "WITH x AS (DELETE FROM users RETURNING 1) SELECT * FROM x",
        },
    )

    assert not result.success  # sqlite3 itself refuses the write

    conn = sqlite3.connect(tmp_path / "app.sqlite")
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    assert count == 2


def test_query_mode_rejects_multiple_statements(tmp_path):
    _make_db(tmp_path / "app.sqlite")

    result = SqlQueryTool().execute(
        _workspace(tmp_path),
        {"db_path": "app.sqlite", "mode": "query", "query": "SELECT 1; SELECT 2"},
    )

    assert not result.success
    assert "single statement" in result.output.lower()


def test_query_mode_requires_query_argument(tmp_path):
    _make_db(tmp_path / "app.sqlite")

    result = SqlQueryTool().execute(_workspace(tmp_path), {"db_path": "app.sqlite", "mode": "query"})

    assert not result.success
    assert "requires a 'query'" in result.output


def test_missing_database_file(tmp_path):
    result = SqlQueryTool().execute(_workspace(tmp_path), {"db_path": "nope.sqlite"})
    assert not result.success
    assert "not found" in result.output.lower()


def test_unsupported_extension(tmp_path):
    (tmp_path / "app.txt").write_text("not a database")
    result = SqlQueryTool().execute(_workspace(tmp_path), {"db_path": "app.txt"})
    assert not result.success
    assert "Unsupported extension" in result.output


def test_path_outside_workspace_is_rejected(tmp_path):
    result = SqlQueryTool().execute(_workspace(tmp_path), {"db_path": "../../etc/passwd"})
    assert not result.success
    assert "outside the workspace" in result.output


def test_query_result_row_cap(tmp_path):
    db_path = tmp_path / "app.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE items (n INTEGER)")
    conn.executemany("INSERT INTO items (n) VALUES (?)", [(i,) for i in range(250)])
    conn.commit()
    conn.close()

    result = SqlQueryTool().execute(
        _workspace(tmp_path),
        {"db_path": "app.sqlite", "mode": "query", "query": "SELECT n FROM items"},
    )

    assert result.success
    assert "truncated at 200 rows" in result.output
