from skytrap.core.context import WorkspaceContext
from skytrap.core.languages.base import LanguageProfile, ResolvedCommands
from skytrap.core.languages.registry import register_language


def _resolve_cpp(workspace: WorkspaceContext) -> ResolvedCommands | None:
    """Same principle as C: an existing CMake/Make/Ninja setup always wins over a
    hand-written compile line."""
    root = workspace.path
    if (root / "CMakeLists.txt").exists():
        return ResolvedCommands(
            build_commands=("cmake -S . -B build", "cmake --build build"),
            test_commands=("ctest --test-dir build",),
        )
    if (root / "Makefile").exists() or (root / "makefile").exists():
        return ResolvedCommands(build_commands=("make",), test_commands=("make test",))
    return ResolvedCommands(build_commands=("clang++ -std=c++20 *.cpp -o app",))


cpp_profile = register_language(
    LanguageProfile(
        id="cpp",
        name="C++",
        extensions=(".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"),
        manifests=("CMakeLists.txt", "meson.build", "BUILD", "BUILD.bazel"),
        package_managers=("vcpkg", "conan"),
        toolchain_executables=("clang++", "g++", "cmake", "make", "ninja"),
        format_commands=("clang-format -i",),
        lint_commands=("clang-tidy",),
        resolve_commands=_resolve_cpp,
        notes="Never invent a compile command when CMake/Make/Ninja/Meson/Bazel already configures the build.",
    )
)
