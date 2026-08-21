from skytrap.core.languages.base import LanguageProfile
from skytrap.core.languages.registry import register_language

go_profile = register_language(
    LanguageProfile(
        id="go",
        name="Go",
        extensions=(".go",),
        manifests=("go.mod", "go.sum"),
        package_managers=("go",),
        check_command="go vet ./...",
        build_commands=("go build ./...",),
        test_commands=("go test ./...",),
        format_commands=("gofmt -w .",),
        lint_commands=("go vet ./...", "golangci-lint run"),
        toolchain_executables=("go", "gofmt", "golangci-lint"),
        notes="Idiomatic Go — goroutines/channels/interfaces, not ported Java/C++ patterns.",
    )
)
