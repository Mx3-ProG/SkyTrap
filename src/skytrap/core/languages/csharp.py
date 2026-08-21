from skytrap.core.languages.base import LanguageProfile
from skytrap.core.languages.registry import register_language

csharp_profile = register_language(
    LanguageProfile(
        id="csharp",
        name="C#",
        extensions=(".cs",),
        manifests=("*.csproj", "*.sln", "Directory.Build.props"),
        package_managers=("nuget",),
        build_commands=("dotnet build",),
        test_commands=("dotnet test",),
        format_commands=("dotnet format",),
        lint_commands=("dotnet build",),  # analyzers run as part of the build
        toolchain_executables=("dotnet",),
        notes="`dotnet restore` before build/test only if dependencies changed — it's a network "
        "operation, not run unconditionally on every task.",
    )
)
