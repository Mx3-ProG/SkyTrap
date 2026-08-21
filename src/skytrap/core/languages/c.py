from skytrap.core.context import WorkspaceContext
from skytrap.core.languages.base import LanguageProfile, ResolvedCommands
from skytrap.core.languages.registry import register_language


def _resolve_c(workspace: WorkspaceContext) -> ResolvedCommands | None:
    """Never invent a compile line if the project already has a build system — CMake
    and Make both encode flags/include paths/link order a hand-written `clang *.c`
    can't reproduce."""
    root = workspace.path
    if (root / "CMakeLists.txt").exists():
        return ResolvedCommands(
            build_commands=("cmake -S . -B build", "cmake --build build"),
            test_commands=("ctest --test-dir build",),
        )
    if (root / "Makefile").exists() or (root / "makefile").exists():
        return ResolvedCommands(build_commands=("make",), test_commands=("make test",))
    return ResolvedCommands(build_commands=("clang *.c -o app",))


c_profile = register_language(
    LanguageProfile(
        id="c",
        name="C",
        extensions=(".c", ".h"),
        manifests=("CMakeLists.txt", "Makefile"),
        package_managers=(),
        toolchain_executables=("gcc", "clang", "make", "cmake"),
        format_commands=("clang-format -i",),
        lint_commands=("clang-tidy",),
        resolve_commands=_resolve_c,
        notes="Always check for an existing CMake/Make build system before compiling by hand.",
    )
)
