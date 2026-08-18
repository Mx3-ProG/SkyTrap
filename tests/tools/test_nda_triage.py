from pathlib import Path

from docx import Document

from skytrap.core.context import WorkspaceContext
from skytrap.tools.skills.nda_triage.tool import NdaTriageTool


def _workspace(tmp_path: Path) -> WorkspaceContext:
    return WorkspaceContext(path=tmp_path.resolve(), name=tmp_path.name, is_git=False)


def _make_docx(path: Path, paragraphs: list[str]) -> None:
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    document.save(str(path))


def test_extracts_docx_and_includes_all_three_triage_bands(tmp_path):
    _make_docx(
        tmp_path / "nda.docx",
        [
            "This Non-Disclosure Agreement is between Acme Corp and Contractor.",
            "This confidentiality obligation shall survive in perpetuity.",
        ],
    )

    result = NdaTriageTool().execute(_workspace(tmp_path), {"path": "nda.docx"})

    assert result.success
    assert "This confidentiality obligation shall survive in perpetuity." in result.output
    assert "RED" in result.output
    assert "YELLOW" in result.output
    assert "GREEN" in result.output
    assert "perpetual" in result.output.lower()  # the RED criterion itself is present


def test_missing_file(tmp_path):
    result = NdaTriageTool().execute(_workspace(tmp_path), {"path": "nope.pdf"})
    assert not result.success
    assert "not found" in result.output.lower()


def test_unsupported_extension(tmp_path):
    (tmp_path / "nda.txt").write_text("plain text nda")
    result = NdaTriageTool().execute(_workspace(tmp_path), {"path": "nda.txt"})
    assert not result.success
    assert "Unsupported file type" in result.output


def test_path_outside_workspace_is_rejected(tmp_path):
    result = NdaTriageTool().execute(_workspace(tmp_path), {"path": "../../etc/passwd"})
    assert not result.success
    assert "outside the workspace" in result.output


def test_invalid_pdf_bytes_fail_gracefully(tmp_path):
    (tmp_path / "broken.pdf").write_bytes(b"not a real pdf")
    result = NdaTriageTool().execute(_workspace(tmp_path), {"path": "broken.pdf"})
    assert not result.success
    assert "Failed to extract text" in result.output


def test_missing_path_argument(tmp_path):
    result = NdaTriageTool().execute(_workspace(tmp_path), {})
    assert not result.success
    assert "Invalid arguments" in result.output


def test_empty_document_fails(tmp_path):
    _make_docx(tmp_path / "empty.docx", [])
    result = NdaTriageTool().execute(_workspace(tmp_path), {"path": "empty.docx"})
    assert not result.success
    assert "No extractable text" in result.output
