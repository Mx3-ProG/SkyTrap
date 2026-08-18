from pydantic import BaseModel, Field, model_validator


class DcfYearAssumption(BaseModel):
    year_label: str = Field(description="Label for this projection year, e.g. '2025' or 'Year 1'")
    revenue: float = Field(gt=0)
    ebitda_margin: float = Field(ge=0, le=1, description="e.g. 0.25 for 25%")
    da_pct_revenue: float = Field(default=0.03, ge=0, le=1, description="Depreciation & amortization as % of revenue")
    capex_pct_revenue: float = Field(default=0.05, ge=0, le=1, description="Capital expenditure as % of revenue")
    nwc_change_pct_revenue: float = Field(
        default=0.0, ge=-1, le=1, description="Change in net working capital as % of revenue"
    )
    tax_rate: float = Field(default=0.21, ge=0, le=1)


class DcfModelInput(BaseModel):
    path: str = Field(description="Output path for the .xlsx file, relative to the workspace root")
    company_name: str = Field(description="Company or project name shown in the model header")
    years: list[DcfYearAssumption] = Field(
        min_length=1, description="Ordered projection years (typically 3-10)"
    )
    discount_rate: float = Field(gt=0, lt=1, description="WACC, e.g. 0.10 for 10%")
    terminal_growth_rate: float = Field(
        ge=0, lt=1, description="Perpetuity growth rate for the terminal value, e.g. 0.02 for 2%"
    )

    @model_validator(mode="after")
    def _discount_rate_must_exceed_terminal_growth(self) -> "DcfModelInput":
        # The Gordon Growth terminal value formula (FCF * (1+g) / (r-g)) is
        # financially meaningless (or divides by zero/negative) if r <= g.
        if self.discount_rate <= self.terminal_growth_rate:
            raise ValueError(
                f"discount_rate ({self.discount_rate}) must be greater than "
                f"terminal_growth_rate ({self.terminal_growth_rate})"
            )
        return self
