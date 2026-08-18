# contract_review

Extracts text from a contract (`.pdf` or `.docx`) and returns it alongside a
clause-by-clause review checklist. The tool itself is purely mechanical
(extraction only, no LLM call inside it) — the actual comparison against the
checklist happens in the calling agent's own reasoning, consistent with every
other tool in SkyTrap.

## Arguments

```json
{"path": "contracts/msa.pdf", "checklist": ["optional", "custom", "items"]}
```

`checklist` is optional — omitted, it falls back to a standard commercial
contract checklist (termination, liability cap, indemnification, IP
ownership, confidentiality, governing law, auto-renewal, payment terms,
assignment, non-compete).

## Dependencies

`pypdf` (PDF), `python-docx` (DOCX) — added via `uv add`.
