from skytrap.core.context import WorkspaceContext
from skytrap.core.language_detection import detect_languages


def _workspace(tmp_path):
    return WorkspaceContext(path=tmp_path, name=tmp_path.name, is_git=False)


def _ids(tmp_path):
    return {m.profile.id for m in detect_languages(_workspace(tmp_path))}


def _manifest_detected(tmp_path, language_id: str) -> bool:
    for match in detect_languages(_workspace(tmp_path)):
        if match.profile.id == language_id:
            return match.manifest_detected
    return False


def test_cargo_toml_detected_as_rust(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'x'\n")
    assert "rust" in _ids(tmp_path)
    assert _manifest_detected(tmp_path, "rust")


def test_cmakelists_detected_as_cpp_and_c(tmp_path):
    (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.10)\n")
    ids = _ids(tmp_path)
    assert "cpp" in ids
    assert "c" in ids


def test_pyproject_toml_detected_as_python(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    assert "python" in _ids(tmp_path)
    assert _manifest_detected(tmp_path, "python")


def test_csproj_detected_as_csharp(tmp_path):
    (tmp_path / "MyApp.csproj").write_text("<Project></Project>")
    assert "csharp" in _ids(tmp_path)
    assert _manifest_detected(tmp_path, "csharp")


def test_gemfile_detected_as_ruby(tmp_path):
    (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\n")
    assert "ruby" in _ids(tmp_path)
    assert _manifest_detected(tmp_path, "ruby")


def test_go_mod_detected_as_go(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/x\n\ngo 1.22\n")
    assert "go" in _ids(tmp_path)
    assert _manifest_detected(tmp_path, "go")


def test_package_json_detected_as_javascript(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "x"}')
    assert "javascript" in _ids(tmp_path)


def test_tsconfig_detected_as_typescript(tmp_path):
    (tmp_path / "tsconfig.json").write_text("{}")
    assert "typescript" in _ids(tmp_path)


def test_no_manifest_and_no_source_files_detects_nothing(tmp_path):
    (tmp_path / "README.md").write_text("hello\n")
    assert _ids(tmp_path) == set()


def test_extension_only_repo_detected_without_manifest(tmp_path):
    (tmp_path / "script.py").write_text("print('hi')\n")
    assert "python" in _ids(tmp_path)
    assert not _manifest_detected(tmp_path, "python")


def test_monorepo_detects_each_subproject_language(tmp_path):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text('{"name": "f"}')
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "Cargo.toml").write_text("[package]\nname = 'a'\n")
    (tmp_path / "api" / "src").mkdir()
    (tmp_path / "api" / "src" / "main.rs").write_text("fn main() {}\n")

    ids = _ids(tmp_path)
    assert "javascript" in ids
    assert "rust" in ids
