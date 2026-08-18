# pitch_deck

Generates a PowerPoint (`.pptx`) deck from structured slides. Three slide
types: `title` (title + optional subtitle), `section` (a section-header
divider), `bullets` (title + a bullet list). Writes a file, so it goes
through the same `confirm` gate as `write_file`.

## Arguments

```json
{
  "path": "decks/pitch.pptx",
  "slides": [
    {"type": "title", "title": "Acme Corp", "subtitle": "Series A pitch"},
    {"type": "section", "title": "The Problem"},
    {"type": "bullets", "title": "Why now", "bullets": ["Market grew 3x in 2 years", "No incumbent solution"]}
  ]
}
```

## Dependencies

`python-pptx` — added via `uv add`.
