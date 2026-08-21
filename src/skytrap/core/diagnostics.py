import re
from dataclasses import dataclass


@dataclass
class Diagnostic:
    file: str | None
    line: int | None
    column: int | None
    severity: str
    code: str | None
    message: str


# gcc/clang: "file.c:12:5: error: message" (also matches clang-tidy/clang-format -n)
_GCC_CLANG = re.compile(
    r"^(?P<file>[^:\n]+):(?P<line>\d+):(?P<column>\d+):\s*(?P<severity>error|warning|note):\s*(?P<message>.+)$",
    re.MULTILINE,
)

# rustc: "error[E0384]: message\n  --> src/main.rs:12:5"
_RUSTC = re.compile(
    r"^(?P<severity>error|warning)(?:\[(?P<code>[^\]]+)\])?:\s*(?P<message>.+)\n"
    r"\s*-->\s*(?P<file>[^:\n]+):(?P<line>\d+):(?P<column>\d+)",
    re.MULTILINE,
)

# go build/vet: "./file.go:12:5: message"
_GO = re.compile(r"^(?P<file>\S+\.go):(?P<line>\d+):(?P<column>\d+):\s*(?P<message>.+)$", re.MULTILINE)

# mypy/ruff: "file.py:12:5: error: message [code]" or "file.py:12: error: message"
_PYTHON = re.compile(
    r"^(?P<file>[^:\n]+\.pyi?):(?P<line>\d+):(?:(?P<column>\d+):)?\s*(?P<severity>error|warning|note):\s*"
    r"(?P<message>.+?)(?:\s+\[(?P<code>[\w-]+)\])?$",
    re.MULTILINE,
)

# dotnet/csc: "file.cs(12,5): error CS0103: message"
_DOTNET = re.compile(
    r"^(?P<file>[^(\n]+)\((?P<line>\d+),(?P<column>\d+)\):\s*(?P<severity>error|warning)\s+"
    r"(?P<code>\w+\d+):\s*(?P<message>.+)$",
    re.MULTILINE,
)

_PATTERNS: dict[str, list[re.Pattern]] = {
    "c": [_GCC_CLANG],
    "cpp": [_GCC_CLANG],
    "rust": [_RUSTC],
    "go": [_GO],
    "python": [_PYTHON],
    "csharp": [_DOTNET],
}


def parse_diagnostics(output: str, language_id: str) -> list[Diagnostic]:
    """Extracts structured file/line/column/severity/message from raw compiler or
    linter output, so a fix loop can jump straight to the failing location instead
    of re-reading the whole error text on every retry. Returns [] for a language
    with no known pattern or output that doesn't match one — never guesses."""
    diagnostics: list[Diagnostic] = []
    for pattern in _PATTERNS.get(language_id, []):
        for match in pattern.finditer(output):
            groups = match.groupdict()
            diagnostics.append(
                Diagnostic(
                    file=groups.get("file"),
                    line=int(groups["line"]) if groups.get("line") else None,
                    column=int(groups["column"]) if groups.get("column") else None,
                    severity=groups.get("severity") or "error",
                    code=groups.get("code"),
                    message=groups["message"].strip(),
                )
            )
    return diagnostics
