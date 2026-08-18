# nda_triage

Extracts text from an NDA (`.pdf` or `.docx`) and pairs it with RED/YELLOW/GREEN
triage criteria. Mechanical only, like `contract_review` — the tool doesn't decide
the classification itself, the calling agent does, citing specific clauses from the
criteria it's given.

## Arguments

```json
{"path": "contracts/vendor-nda.pdf"}
```

## Triage bands

- **RED** — do not sign without legal review (e.g. non-compete bundled in, perpetual
  confidentiality, one-sided/uncapped indemnification, unrestricted assignment,
  unusual jurisdiction).
- **YELLOW** — negotiate before signing (e.g. long-but-not-perpetual term, one-way
  when mutual is expected, overly broad "Confidential Information" definition,
  missing standard carve-outs).
- **GREEN** — standard, low-risk (mutual, standard term, standard carve-outs, no
  non-compete, reasonable mutual remedies).

## Dependencies

`pypdf` (PDF), `python-docx` (DOCX) — already added for `contract_review`, no new
`uv add` needed.
