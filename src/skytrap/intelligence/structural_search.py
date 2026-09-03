"""Item 6 — structural code search.

Prefers the `ast-grep` CLI (real AST pattern matching) when it's on PATH.
Falls back to the SymbolIndex (declarations) plus a ripgrep call-site search
(an approximation, not true AST matching — reported honestly via
`StructuralMatch.approximate`) when ast-grep isn't installed.

This module is read-only. Any actual code change SkyTrap wants to make based
on a structural match still goes through write_file/delete_file — i.e. the
same RiskEngine/diff/rollback path as every other mutation. No separate
"ast-grep can edit files" code path exists.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

from pydantic import BaseModel

from skytrap.core.context import WorkspaceContext
from skytrap.intelligence.symbols import SymbolIndex


class StructuralMatch(BaseModel):
    file: str
    line: int
    text: str
    approximate: bool = False


def ast_grep_binary() -> str | None:
    for candidate in ("ast-grep", "sg"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


class StructuralSearch:
    """`search()` finds declarations/calls/patterns; `find_calls()` is a
    convenience for the single most common query ("who calls X")."""

    def __init__(self, symbol_index: SymbolIndex | None = None) -> None:
        self.symbol_index = symbol_index

    def backend(self) -> str:
        return "ast-grep" if ast_grep_binary() else "tree-sitter+ripgrep (approximate)"

    def search(
        self, workspace: WorkspaceContext, pattern: str, *, language: str | None = None
    ) -> list[StructuralMatch]:
        binary = ast_grep_binary()
        if binary:
            return self._search_ast_grep(binary, workspace, pattern, language)
        return self._search_fallback(workspace, pattern)

    def find_calls(self, workspace: WorkspaceContext, function_name: str) -> list[StructuralMatch]:
        return self.search(workspace, f"{function_name}($$$ARGS)")

    @staticmethod
    def _search_ast_grep(
        binary: str, workspace: WorkspaceContext, pattern: str, language: str | None
    ) -> list[StructuralMatch]:
        command = [binary, "run", "--pattern", pattern, "--json"]
        if language:
            command += ["--lang", language]
        command.append(".")
        try:
            result = subprocess.run(
                command, cwd=workspace.path, capture_output=True, text=True, timeout=20
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return []
        if result.returncode not in (0, 1) or not result.stdout.strip():
            return []
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []
        matches: list[StructuralMatch] = []
        for item in payload:
            try:
                matches.append(
                    StructuralMatch(
                        file=item["file"],
                        line=item["range"]["start"]["line"] + 1,
                        text=item.get("text", item.get("lines", "")).strip()[:200],
                    )
                )
            except (KeyError, TypeError):
                continue
        return matches

    def _search_fallback(self, workspace: WorkspaceContext, pattern: str) -> list[StructuralMatch]:
        """Approximate structural search: a symbol-index declaration lookup
        plus a ripgrep call-site scan for `<name>(`. Honest about not being a
        real AST match — see `StructuralMatch.approximate`."""
        name_match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(", pattern.strip())
        target = name_match.group(1) if name_match else pattern.strip()
        matches: list[StructuralMatch] = []

        if self.symbol_index is not None:
            for entry in self.symbol_index.find(target):
                matches.append(
                    StructuralMatch(
                        file=entry.file,
                        line=entry.start_line,
                        text=f"{entry.kind} {entry.name}",
                        approximate=True,
                    )
                )

        try:
            result = subprocess.run(
                ["rg", "--line-number", "--no-heading", "--color=never", re.escape(target) + r"\s*\("],
                cwd=workspace.path,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return matches
        if result.returncode in (0, 1):
            for line in result.stdout.splitlines()[:50]:
                parts = line.split(":", 2)
                if len(parts) == 3:
                    file, line_no, text = parts
                    matches.append(
                        StructuralMatch(file=file, line=int(line_no), text=text.strip()[:200], approximate=True)
                    )
        return matches
