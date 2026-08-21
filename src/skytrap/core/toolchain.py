import shutil

# The union of every profile's toolchain_executables, plus a few always worth
# knowing about. Kept as a flat list (not derived from the registry) so
# `detect_toolchain()` doesn't need every language imported just to run.
KNOWN_EXECUTABLES = (
    "python3", "pip", "pipx", "poetry", "uv", "ruff", "mypy", "pytest",
    "node", "npm", "pnpm", "yarn", "bun", "tsc", "eslint", "prettier",
    "gcc", "clang", "clang++", "g++", "cmake", "make", "ninja", "clang-format", "clang-tidy",
    "cargo", "rustc",
    "dotnet",
    "ruby", "gem", "bundle", "rspec", "rubocop",
    "go", "gofmt", "golangci-lint",
    "java", "javac", "mvn", "gradle",
    "kotlin", "kotlinc",
    "swift",
    "php", "composer", "phpunit",
    "psql", "mysql", "sqlite3",
    "bash", "zsh", "sh", "pwsh",
    "git",
)


def detect_toolchain(executables: tuple[str, ...] = KNOWN_EXECUTABLES) -> dict[str, str | None]:
    """Real `which`-equivalent lookups (shutil.which, which also works on Windows
    against PATHEXT) — never assume a toolchain is present just because a language
    was detected in the repo."""
    return {name: shutil.which(name) for name in executables}
