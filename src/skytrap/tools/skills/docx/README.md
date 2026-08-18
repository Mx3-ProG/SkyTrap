# docx

Generates a Word (`.docx`) document from structured content blocks (headings,
paragraphs, bullets). Unlike the read-only skills (`contract_review`,
`nda_triage`, `sql_queries`), this one writes a file — so it goes through the
same `confirm` gate as `write_file`: a text preview of the document's content
is shown, and nothing is written until the user approves.

## Arguments

```json
{
  "path": "reports/summary.docx",
  "blocks": [
    {"type": "heading", "text": "Q3 Summary", "level": 1},
    {"type": "paragraph", "text": "Revenue grew 12% quarter over quarter."},
    {"type": "bullet", "text": "New customers: 340"},
    {"type": "bullet", "text": "Churn: 2.1%"}
  ]
}
```

## Dependencies

`python-docx` — already added for `contract_review`, no new `uv add` needed.
