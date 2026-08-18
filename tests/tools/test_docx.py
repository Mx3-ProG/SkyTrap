from pathlib import Path

from docx import Document

from skytrap.core.context import WorkspaceContext
from skytrap.tools.skills.docx.tool import DocxGenerateTool


def _workspace(tmp_path: Path) -> WorkspaceContext:
    return WorkspaceContext(path=tmp_path.resolve(), name=tmp_path.name, is_git=False)


def test_confirmed_write_creates_real_docx_with_correct_content(tmp_path):
    previews = []

    def confirm(preview: str) -> bool:
        previews.append(preview)
        return True

    tool = DocxGenerateTool(confirm=confirm)
    result = tool.execute(
        _workspace(tmp_path),
        {
            "path": "report.docx",
            "blocks": [
                {"type": "heading", "text": "Q3 Summary", "level": 1},
                {"type": "paragraph", "text": "Revenue grew 12%."},
                {"type": "bullet", "text": "New customers: 340"},
            ],
        },
    )

    assert result.success
    assert (tmp_path / "report.docx").exists()

    document = Document(str(tmp_path / "report.docx"))
    texts = [p.text for p in document.paragraphs]
    assert "Q3 Summary" in texts
    assert "Revenue grew 12%." in texts
    assert "New customers: 340" in texts

    # confirm() was shown a real preview of the content, not called blind
    assert "Q3 Summary" in previews[0]
    assert "Revenue grew 12%." in previews[0]


def test_declined_write_creates_no_file(tmp_path):
    tool = DocxGenerateTool(confirm=lambda preview: False)

    result = tool.execute(
        _workspace(tmp_path),
        {"path": "report.docx", "blocks": [{"type": "paragraph", "text": "Should not be written"}]},
    )

    assert not result.success
    assert "declined" in result.output.lower()
    assert not (tmp_path / "report.docx").exists()


def test_rejects_non_docx_extension(tmp_path):
    tool = DocxGenerateTool(confirm=lambda preview: True)
    result = tool.execute(
        _workspace(tmp_path), {"path": "report.txt", "blocks": [{"type": "paragraph", "text": "x"}]}
    )
    assert not result.success
    assert ".docx" in result.output


def test_rejects_empty_blocks(tmp_path):
    tool = DocxGenerateTool(confirm=lambda preview: True)
    result = tool.execute(_workspace(tmp_path), {"path": "report.docx", "blocks": []})
    assert not result.success
    assert "at least one" in result.output


def test_path_outside_workspace_is_rejected(tmp_path):
    tool = DocxGenerateTool(confirm=lambda preview: True)
    result = tool.execute(
        _workspace(tmp_path),
        {"path": "../../etc/evil.docx", "blocks": [{"type": "paragraph", "text": "x"}]},
    )
    assert not result.success
    assert "outside the workspace" in result.output


def test_invalid_arguments(tmp_path):
    tool = DocxGenerateTool(confirm=lambda preview: True)
    result = tool.execute(_workspace(tmp_path), {"path": "report.docx"})  # missing 'blocks'
    assert not result.success
    assert "Invalid arguments" in result.output


def test_creates_parent_directories(tmp_path):
    tool = DocxGenerateTool(confirm=lambda preview: True)
    result = tool.execute(
        _workspace(tmp_path),
        {"path": "nested/dir/report.docx", "blocks": [{"type": "paragraph", "text": "x"}]},
    )
    assert result.success
    assert (tmp_path / "nested" / "dir" / "report.docx").exists()
