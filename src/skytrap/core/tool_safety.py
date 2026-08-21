import re
import shlex
from typing import Literal

Tier = Literal["SAFE", "CONFIRM", "DESTRUCTIVE"]
CommandTier = Literal["SAFE", "CONFIRM", "DESTRUCTIVE", "FORBIDDEN"]

FORBIDDEN_PATTERNS = [
    r"\bsudo\b",
    r"\brm\s+-rf\s+/(\s|$)",
    r"\bdiskutil\b",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bhalt\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
]

SAFE_PREFIXES = [
    ("ls",),
    ("pwd",),
    ("cat",),
    ("find",),
    ("grep",),
    ("rg",),
    ("wc",),
    ("head",),
    ("tail",),
    ("which",),
    ("echo",),
    ("git", "status"),
    ("git", "diff"),
    ("git", "log"),
    ("git", "branch"),
    ("git", "show"),
    ("npm", "test"),
    ("npm", "run", "test"),
    ("yarn", "test"),
    ("pytest",),
    ("python", "-m", "pytest"),
    ("python3", "-m", "pytest"),
    ("uv", "run", "pytest"),
    ("ruff", "check"),
    ("ruff", "format"),
    ("mypy",),
    ("tsc",),
    ("eslint",),
    # Compiling/checking/building/testing/formatting only touches build output and
    # source formatting in the workspace — it doesn't run arbitrary user code with
    # side effects the way `cargo run`/`go run`/`dotnet run`/a bare `python3 x.py` do.
    ("cargo", "check"),
    ("cargo", "build"),
    ("cargo", "test"),
    ("cargo", "fmt"),
    ("cargo", "clippy"),
    ("go", "build"),
    ("go", "test"),
    ("go", "vet"),
    ("gofmt",),
    ("golangci-lint",),
    ("dotnet", "build"),
    ("dotnet", "test"),
    ("dotnet", "format"),
    ("gcc",),
    ("clang",),
    ("g++",),
    ("clang++",),
    ("make",),
    ("cmake", "--build"),
    ("clang-format",),
    ("clang-tidy",),
    ("mvn", "test"),
    ("mvn", "package"),
    ("mvn", "compile"),
    ("gradle", "build"),
    ("gradle", "test"),
    ("./gradlew", "build"),
    ("./gradlew", "test"),
    ("bundle", "exec", "rspec"),
    ("bundle", "exec", "rails", "test"),
    ("rubocop",),
    ("swift", "build"),
    ("swift", "test"),
    ("phpunit",),
]

# Medium risk: installs deps or runs arbitrary code, but doesn't destroy anything by
# itself. Auto-approved (shown, not asked) in "auto" mode; asked in "normal" mode.
CONFIRM_PREFIXES = [
    ("npm", "install"),
    ("npm", "ci"),
    ("npm", "run"),
    ("yarn", "install"),
    ("pip", "install"),
    ("uv", "pip", "install"),
    ("git", "add"),
    ("git", "commit"),
    ("python",),
    ("python3",),
    ("node",),
    ("cargo", "run"),
    ("go", "run"),
    ("dotnet", "run"),
    ("dotnet", "restore"),
    ("swift", "run"),
    ("gem", "install"),
    ("bundle", "install"),
    ("composer", "install"),
    ("composer", "require"),
    ("cmake", "-S"),
]

# Can lose committed/uncommitted work or rewrite shared history. Always asks,
# regardless of mode — no mode is allowed to bypass this tier.
DESTRUCTIVE_PREFIXES = [
    ("git", "reset"),
    ("git", "push"),
    ("git", "checkout"),
    ("git", "merge"),
    ("git", "rebase"),
    ("rm",),
    ("mv",),
]


def classify_command(command: str) -> CommandTier:
    if any(re.search(pattern, command) for pattern in FORBIDDEN_PATTERNS):
        return "FORBIDDEN"

    try:
        tokens = tuple(shlex.split(command))
    except ValueError:
        return "CONFIRM"  # unparsable quoting — don't guess, ask the user

    for prefix in SAFE_PREFIXES:
        if tokens[: len(prefix)] == prefix:
            return "SAFE"

    for prefix in DESTRUCTIVE_PREFIXES:
        if tokens[: len(prefix)] == prefix:
            return "DESTRUCTIVE"

    for prefix in CONFIRM_PREFIXES:
        if tokens[: len(prefix)] == prefix:
            return "CONFIRM"

    # Unknown command: default to the safer choice, not silent execution.
    return "CONFIRM"


SENSITIVE_PATH_PATTERNS = [
    r"(^|/)\.env(\..*)?$",
    r"secret",
    r"credential",
    r"\.pem$",
    r"(^|/)id_rsa",
    r"\.key$",
    r"(^|/)\.git/",
]


def classify_path(path: str) -> Tier:
    """Used by write_file/delete_file: SAFE by default (git-recoverable, ordinary
    dev work), DESTRUCTIVE for anything that looks like a secret/credential so those
    always confirm regardless of mode."""
    lowered = path.lower()
    if any(re.search(pattern, lowered) for pattern in SENSITIVE_PATH_PATTERNS):
        return "DESTRUCTIVE"
    return "SAFE"
