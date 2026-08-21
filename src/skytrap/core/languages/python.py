from skytrap.core.context import WorkspaceContext
from skytrap.core.languages.base import LanguageProfile, ResolvedCommands
from skytrap.core.languages.registry import register_language


def _resolve_python(workspace: WorkspaceContext) -> ResolvedCommands | None:
    root = workspace.path
    # Prefer the project's own dependency manager's run-prefix so the right venv is
    # used, instead of assuming a bare `pytest`/`ruff` on PATH.
    if (root / "uv.lock").exists() or (root / "pyproject.toml").exists() and _uses_uv(root):
        prefix = "uv run"
    elif (root / "poetry.lock").exists():
        prefix = "poetry run"
    else:
        prefix = ""

    def cmd(tool: str) -> str:
        return f"{prefix} {tool}".strip()

    test_commands: tuple[str, ...]
    if (root / "pyproject.toml").exists() or (root / "pytest.ini").exists() or (root / "setup.cfg").exists():
        test_commands = (cmd("pytest"),)
    else:
        test_commands = (cmd("python3 -m unittest"),)

    return ResolvedCommands(
        check_command=cmd("ruff check ."),
        test_commands=test_commands,
        format_commands=(cmd("ruff format ."),),
        lint_commands=(cmd("ruff check ."), cmd("mypy .")),
    )


def _uses_uv(root) -> bool:
    try:
        content = (root / "pyproject.toml").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return "[tool.uv]" in content or "uv.lock" in [p.name for p in root.glob("uv.lock")]


python_profile = register_language(
    LanguageProfile(
        id="python",
        name="Python",
        extensions=(".py", ".pyi"),
        manifests=("pyproject.toml", "requirements.txt", "Pipfile", "setup.py", "setup.cfg"),
        package_managers=("pip", "pipx", "poetry", "uv", "pdm"),
        toolchain_executables=("python3", "pip", "pipx", "poetry", "uv", "ruff", "mypy", "pytest"),
        resolve_commands=_resolve_python,
        notes="Dependency manager is inferred from uv.lock/poetry.lock before falling back to a "
        "bare venv/pip project — never installs with a different manager than the one already in use.",
    )
)
