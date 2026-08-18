# sql_queries

Text-to-SQL against a SQLite database in the current workspace. `mode="schema"`
lists tables and columns (use this first); `mode="query"` runs a read-only
SELECT/WITH/EXPLAIN statement and returns the rows.

The tool never writes: the database is opened with `mode=ro` at the SQLite
driver level, so even if a mutating statement somehow passed the read-only
prefix check, the OS-level read-only open makes executing it impossible.

## Arguments

```json
{"db_path": "data/app.sqlite", "mode": "schema"}
{"db_path": "data/app.sqlite", "mode": "query", "query": "SELECT * FROM users LIMIT 10"}
```

## Limitations

- Single statement only — no `;` inside the query, including inside string
  literals (a deliberately strict default to avoid multi-statement injection).
- Results capped at 200 rows.
- Scoped to `.db`/`.sqlite`/`.sqlite3` files inside the workspace — targets
  the *project's own* database, not SkyTrap's internal `~/.skytrap/skytrap.db`.

## Dependencies

None new — `sqlite3` is Python stdlib.
