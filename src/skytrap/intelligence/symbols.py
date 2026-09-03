"""Item 4 — local, incremental symbol index built on top of CodeParser."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from skytrap.core.context import WorkspaceContext
from skytrap.intelligence.parser import CodeParser, ParsedFile
from skytrap.tools.filesystem import IGNORED_DIRS

MAX_INDEXED_FILES = 1500


class SymbolEntry(BaseModel):
    name: str
    kind: str
    file: str
    start_line: int
    end_line: int


class SymbolIndex:
    """`name -> [SymbolEntry, ...]` plus per-file import/export bookkeeping.
    Incremental: `update_file`/`remove_file` only re-parse the one file that
    changed, so a patch mid-task doesn't require rebuilding everything."""

    def __init__(self, parser: CodeParser | None = None) -> None:
        self.parser = parser or CodeParser()
        self._by_name: dict[str, list[SymbolEntry]] = {}
        self._by_file: dict[str, ParsedFile] = {}
        self.indexed_files: int = 0
        self.skipped_unsupported: int = 0

    @classmethod
    def restore_from(cls, parsed_files: dict[str, dict]) -> "SymbolIndex":
        """Item 3 — CONSUME REPOSITORY MEMORY. Reconstructs an index directly from
        previously-serialized `ParsedFile`s (see `RepositoryMemory.parsed_files`)
        — no tree-sitter re-parsing at all. This is the actual mechanism behind
        `discovery_time_saved`: skipped when the fingerprint doesn't match (the
        current repository state is always the source of truth; a stale cache is
        never preferred over it)."""
        index = cls()
        for path, data in parsed_files.items():
            try:
                parsed = ParsedFile.model_validate(data)
            except Exception:  # noqa: BLE001 - a corrupt cache entry is skipped, never trusted blindly
                continue
            index._by_file[path] = parsed
            for symbol in parsed.symbols:
                index._by_name.setdefault(symbol.name, []).append(
                    SymbolEntry(
                        name=symbol.name,
                        kind=symbol.kind,
                        file=path,
                        start_line=symbol.start_line,
                        end_line=symbol.end_line,
                    )
                )
            index.indexed_files += 1
        return index

    def build(self, workspace: WorkspaceContext, *, max_files: int = MAX_INDEXED_FILES) -> "SymbolIndex":
        count = 0
        for path in sorted(workspace.path.rglob("*")):
            if count >= max_files:
                break
            if not path.is_file():
                continue
            if any(part in IGNORED_DIRS for part in path.relative_to(workspace.path).parts):
                continue
            relative = path.relative_to(workspace.path).as_posix()
            if self.parser.language_for_path(relative) is None:
                continue
            self.update_file(workspace, relative)
            count += 1
        return self

    def update_file(self, workspace: WorkspaceContext, relative_path: str) -> ParsedFile | None:
        self.remove_file(relative_path)
        absolute = workspace.path / relative_path
        parsed = self.parser.parse_file(absolute, relative_path=relative_path)
        if parsed is None:
            self.skipped_unsupported += 1
            return None
        self._by_file[relative_path] = parsed
        for symbol in parsed.symbols:
            self._by_name.setdefault(symbol.name, []).append(
                SymbolEntry(
                    name=symbol.name,
                    kind=symbol.kind,
                    file=relative_path,
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                )
            )
        self.indexed_files += 1
        return parsed

    def remove_file(self, relative_path: str) -> None:
        previous = self._by_file.pop(relative_path, None)
        if previous is None:
            return
        for symbol in previous.symbols:
            entries = self._by_name.get(symbol.name)
            if not entries:
                continue
            self._by_name[symbol.name] = [e for e in entries if e.file != relative_path]
            if not self._by_name[symbol.name]:
                del self._by_name[symbol.name]

    def find(self, name: str) -> list[SymbolEntry]:
        exact = self._by_name.get(name)
        if exact:
            return list(exact)
        lowered = name.lower()
        return [entry for entries in self._by_name.values() for entry in entries if lowered in entry.name.lower()]

    def all_names(self) -> list[str]:
        return sorted(self._by_name.keys())

    def parsed_file(self, relative_path: str) -> ParsedFile | None:
        return self._by_file.get(relative_path)

    def files(self) -> list[str]:
        return sorted(self._by_file.keys())
