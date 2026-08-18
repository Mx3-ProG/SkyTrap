# dcf_model

Generates a Discounted Cash Flow (DCF) valuation model as a real `.xlsx`
workbook with **live formulas**, not static computed numbers — open it in
Excel, change an assumption (revenue, margin, WACC, terminal growth), and
everything downstream recalculates.

Writes a file, so it goes through the same `confirm` gate as `write_file`: a
text preview of the assumptions is shown before anything is written.

## Model structure

For each projection year: Revenue → EBITDA (margin) → D&A → EBIT → Taxes →
NOPAT → + D&A − CapEx − ΔNWC = Unlevered FCF → discount factor → PV of FCF.
Terminal value (Gordon growth) is computed off the final year's FCF and
discounted back. Enterprise Value = sum of all PV(FCF) + PV(terminal value).

## Arguments

```json
{
  "path": "models/acme-dcf.xlsx",
  "company_name": "Acme Corp",
  "years": [
    {"year_label": "2025", "revenue": 1000000, "ebitda_margin": 0.25},
    {"year_label": "2026", "revenue": 1200000, "ebitda_margin": 0.27}
  ],
  "discount_rate": 0.10,
  "terminal_growth_rate": 0.02
}
```

`discount_rate` must be greater than `terminal_growth_rate` (enforced by the
input schema) — otherwise the terminal value formula is financially
meaningless.

## Dependencies

`openpyxl` — added via `uv add`.
