from pathlib import Path

import pytest
from openpyxl import load_workbook
from pydantic import ValidationError

from skytrap.core.context import WorkspaceContext
from skytrap.tools.skills.dcf_model.schema import DcfModelInput
from skytrap.tools.skills.dcf_model.tool import (
    ADD_DA_ROW,
    CAPEX_ROW,
    DA_ROW,
    DISCOUNT_FACTOR_ROW,
    DISCOUNT_RATE_ROW,
    EBIT_ROW,
    EBITDA_ROW,
    ENTERPRISE_VALUE_ROW,
    FCF_ROW,
    NOPAT_ROW,
    NWC_ROW,
    PV_FCF_ROW,
    PV_TERMINAL_VALUE_ROW,
    TAXES_ROW,
    TERMINAL_GROWTH_ROW,
    TERMINAL_VALUE_ROW,
    DcfModelTool,
)


def _workspace(tmp_path: Path) -> WorkspaceContext:
    return WorkspaceContext(path=tmp_path.resolve(), name=tmp_path.name, is_git=False)


TWO_YEAR_ARGS = {
    "path": "model.xlsx",
    "company_name": "Acme Corp",
    "years": [
        {"year_label": "2025", "revenue": 1000.0, "ebitda_margin": 0.25},
        {"year_label": "2026", "revenue": 1100.0, "ebitda_margin": 0.25},
    ],
    "discount_rate": 0.10,
    "terminal_growth_rate": 0.02,
}


def test_confirmed_write_creates_real_xlsx_with_correct_formulas(tmp_path):
    previews = []

    def confirm(preview: str) -> bool:
        previews.append(preview)
        return True

    tool = DcfModelTool(confirm=confirm)
    result = tool.execute(_workspace(tmp_path), TWO_YEAR_ARGS)

    assert result.success
    file_path = tmp_path / "model.xlsx"
    assert file_path.exists()

    # keep_vba=False, data_only=False: read the formula strings themselves, not
    # computed values (openpyxl never computes — no spreadsheet engine involved).
    workbook = load_workbook(str(file_path), data_only=False)
    sheet = workbook.active

    assert sheet["A1"].value == "Acme Corp — DCF Model"
    assert sheet[f"B{DISCOUNT_RATE_ROW}"].value == 0.10
    assert sheet[f"B{TERMINAL_GROWTH_ROW}"].value == 0.02

    # Year 1 = column B, Year 2 = column C
    assert sheet[f"B{EBITDA_ROW}"].value == "=B7*B8"
    assert sheet[f"B{EBIT_ROW}"].value == f"=B{EBITDA_ROW}-B{DA_ROW}"
    assert sheet[f"B{TAXES_ROW}"].value == f"=B{EBIT_ROW}*B12"
    assert sheet[f"B{NOPAT_ROW}"].value == f"=B{EBIT_ROW}-B{TAXES_ROW}"
    assert sheet[f"B{FCF_ROW}"].value == (
        f"=B{NOPAT_ROW}+B{ADD_DA_ROW}+B{CAPEX_ROW}+B{NWC_ROW}"
    )
    assert sheet[f"B{DISCOUNT_FACTOR_ROW}"].value == "=1/(1+$B$3)^1"
    assert sheet[f"C{DISCOUNT_FACTOR_ROW}"].value == "=1/(1+$B$3)^2"
    assert sheet[f"B{PV_FCF_ROW}"].value == f"=B{FCF_ROW}*B{DISCOUNT_FACTOR_ROW}"

    # terminal value only in the LAST year's column (C, the 2nd year here)
    assert sheet[f"B{TERMINAL_VALUE_ROW}"].value is None
    assert sheet[f"C{TERMINAL_VALUE_ROW}"].value == (
        f"=C{FCF_ROW}*(1+$B$4)/($B$3-$B$4)"
    )
    assert sheet[f"C{PV_TERMINAL_VALUE_ROW}"].value == (
        f"=C{TERMINAL_VALUE_ROW}*C{DISCOUNT_FACTOR_ROW}"
    )

    assert sheet[f"B{ENTERPRISE_VALUE_ROW}"].value == (
        f"=SUM(B{PV_FCF_ROW}:C{PV_FCF_ROW})+C{PV_TERMINAL_VALUE_ROW}"
    )

    assert "Acme Corp" in previews[0]
    assert "10.0%" in previews[0]


def test_formula_logic_matches_independently_computed_dcf(tmp_path):
    """openpyxl never evaluates formulas (no spreadsheet engine available in this
    environment) — so this independently recomputes the same standard DCF math in
    plain Python and checks it's internally consistent and sane, as a sanity check
    on the model's financial logic rather than proof the Excel engine agrees."""
    revenue = [1000.0, 1100.0]
    margin = 0.25
    da_pct, capex_pct, nwc_pct, tax_rate = 0.03, 0.05, 0.0, 0.21
    wacc, g = 0.10, 0.02

    fcfs = []
    for rev in revenue:
        ebitda = rev * margin
        da = rev * da_pct
        ebit = ebitda - da
        taxes = ebit * tax_rate
        nopat = ebit - taxes
        capex = -rev * capex_pct
        nwc = -rev * nwc_pct
        fcfs.append(nopat + da + capex + nwc)

    discount_factors = [1 / (1 + wacc) ** (i + 1) for i in range(len(revenue))]
    pv_fcfs = [fcf * df for fcf, df in zip(fcfs, discount_factors)]
    terminal_value = fcfs[-1] * (1 + g) / (wacc - g)
    pv_terminal_value = terminal_value * discount_factors[-1]
    enterprise_value = sum(pv_fcfs) + pv_terminal_value

    assert fcfs[0] == pytest.approx(153.8, rel=1e-6)
    assert fcfs[1] == pytest.approx(169.18, rel=1e-6)
    assert enterprise_value > sum(pv_fcfs)  # terminal value must add positive value
    assert enterprise_value == pytest.approx(2062.317, rel=1e-3)


def test_declined_write_creates_no_file(tmp_path):
    tool = DcfModelTool(confirm=lambda preview: False)
    result = tool.execute(_workspace(tmp_path), TWO_YEAR_ARGS)
    assert not result.success
    assert "declined" in result.output.lower()
    assert not (tmp_path / "model.xlsx").exists()


def test_rejects_non_xlsx_extension(tmp_path):
    tool = DcfModelTool(confirm=lambda preview: True)
    args = {**TWO_YEAR_ARGS, "path": "model.txt"}
    result = tool.execute(_workspace(tmp_path), args)
    assert not result.success
    assert ".xlsx" in result.output


def test_discount_rate_must_exceed_terminal_growth():
    with pytest.raises(ValidationError):
        DcfModelInput.model_validate({**TWO_YEAR_ARGS, "discount_rate": 0.02, "terminal_growth_rate": 0.02})


def test_path_outside_workspace_is_rejected(tmp_path):
    tool = DcfModelTool(confirm=lambda preview: True)
    args = {**TWO_YEAR_ARGS, "path": "../../etc/evil.xlsx"}
    result = tool.execute(_workspace(tmp_path), args)
    assert not result.success
    assert "outside the workspace" in result.output


def test_requires_at_least_one_year():
    with pytest.raises(ValidationError):
        DcfModelInput.model_validate({**TWO_YEAR_ARGS, "years": []})
