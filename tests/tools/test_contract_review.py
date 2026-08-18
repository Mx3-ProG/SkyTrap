from pathlib import Path

from docx import Document

from skytrap.core.context import WorkspaceContext
from skytrap.tools.skills.contract_review.schema import DEFAULT_CHECKLIST
from skytrap.tools.skills.contract_review.tool import ContractReviewTool


def _workspace(tmp_path: Path) -> WorkspaceContext:
    return WorkspaceContext(path=tmp_path.resolve(), name=tmp_path.name, is_git=False)


def _make_docx(path: Path, paragraphs: list[str]) -> None:
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    document.save(str(path))


def _make_minimal_pdf(path: Path, text: str) -> None:
    """Hand-built minimal single-page PDF with real, pypdf-extractable text content —
    avoids adding a PDF-generation library as a test-only dependency for a single
    fixture file."""
    content_stream = f"BT /F1 12 Tf 10 50 Td ({text}) Tj ET".encode()
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/Resources<</Font<</F1 4 0 R>>>>"
        b"/MediaBox[0 0 200 100]/Contents 5 0 R>>",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        b"<</Length " + str(len(content_stream)).encode() + b">>\nstream\n"
        + content_stream
        + b"\nendstream",
    ]

    buffer = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objects, start=1):
        offsets.append(len(buffer))
        buffer += f"{i} 0 obj".encode() + b"\n" + body + b"\nendobj\n"

    xref_offset = len(buffer)
    buffer += f"xref\n0 {len(objects) + 1}\n".encode()
    buffer += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        buffer += f"{offset:010} 00000 n \n".encode()
    buffer += (
        f"trailer<</Size {len(objects) + 1}/Root 1 0 R>>\nstartxref\n{xref_offset}\n%%EOF"
    ).encode()

    path.write_bytes(bytes(buffer))


def test_extracts_docx_and_includes_default_checklist(tmp_path):
    _make_docx(tmp_path / "contract.docx", ["This Agreement is between A and B.", "Termination: 30 days notice."])

    result = ContractReviewTool().execute(_workspace(tmp_path), {"path": "contract.docx"})

    assert result.success
    assert "This Agreement is between A and B." in result.output
    assert "Termination: 30 days notice." in result.output
    assert DEFAULT_CHECKLIST[0] in result.output


def test_extracts_pdf_real_text(tmp_path):
    _make_minimal_pdf(tmp_path / "contract.pdf", "Hello PDF contract test")

    result = ContractReviewTool().execute(_workspace(tmp_path), {"path": "contract.pdf"})

    assert result.success
    assert "Hello PDF contract test" in result.output


def test_custom_checklist_overrides_default(tmp_path):
    _make_docx(tmp_path / "contract.docx", ["Some contract text."])

    result = ContractReviewTool().execute(
        _workspace(tmp_path), {"path": "contract.docx", "checklist": ["My custom item"]}
    )

    assert result.success
    assert "My custom item" in result.output
    assert DEFAULT_CHECKLIST[0] not in result.output


def test_missing_file(tmp_path):
    result = ContractReviewTool().execute(_workspace(tmp_path), {"path": "nope.pdf"})
    assert not result.success
    assert "not found" in result.output.lower()


def test_unsupported_extension(tmp_path):
    (tmp_path / "contract.txt").write_text("plain text contract")
    result = ContractReviewTool().execute(_workspace(tmp_path), {"path": "contract.txt"})
    assert not result.success
    assert "Unsupported file type" in result.output


def test_path_outside_workspace_is_rejected(tmp_path):
    result = ContractReviewTool().execute(_workspace(tmp_path), {"path": "../../etc/passwd"})
    assert not result.success
    assert "outside the workspace" in result.output


def test_invalid_pdf_bytes_fail_gracefully(tmp_path):
    (tmp_path / "broken.pdf").write_bytes(b"not a real pdf")
    result = ContractReviewTool().execute(_workspace(tmp_path), {"path": "broken.pdf"})
    assert not result.success
    assert "Failed to extract text" in result.output


def test_missing_path_argument(tmp_path):
    result = ContractReviewTool().execute(_workspace(tmp_path), {})
    assert not result.success
    assert "Invalid arguments" in result.output
