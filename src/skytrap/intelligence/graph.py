"""Item 5 — a lightweight dependency graph so the planner can ask "what's
likely impacted if this file changes?" and keep the change surface minimal.

Deliberately best-effort: relative JS/TS imports are resolved to files on
disk; Python module imports are resolved when they map onto a file in the
workspace; bare package imports (react, os, ...) are recorded as external and
never resolved to a workspace file.
"""

from __future__ import annotations

from pathlib import PurePosixPath

import re as _re

from skytrap.intelligence.symbols import SymbolIndex

_JS_FROM = _re.compile(r"""from\s+["']([^"']+)["']""")
_JS_REQUIRE = _re.compile(r"""require\(\s*["']([^"']+)["']\s*\)""")
_PY_IMPORT = _re.compile(r"^\s*import\s+([\w.]+)")
_PY_FROM = _re.compile(r"^\s*from\s+([\w.]+)\s+import")

_JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.tsx", "/index.js", "/index.jsx")


class DependencyGraph:
    """`edges[file] = {files it imports}`. `impacted_by(file)` walks the
    reverse edges to answer "who breaks if I change this file"."""

    def __init__(self) -> None:
        self.edges: dict[str, set[str]] = {}
        self.reverse: dict[str, set[str]] = {}
        self.external: dict[str, set[str]] = {}

    def build(self, index: SymbolIndex, all_files: list[str]) -> "DependencyGraph":
        file_set = set(all_files)
        for file in index.files():
            parsed = index.parsed_file(file)
            if parsed is None:
                continue
            for raw_import in parsed.imports:
                resolved = self._resolve(file, raw_import, file_set)
                if resolved:
                    self._add_edge(file, resolved)
                else:
                    module = self._module_of(raw_import)
                    if module:
                        self.external.setdefault(file, set()).add(module)
        return self

    def _add_edge(self, source: str, target: str) -> None:
        self.edges.setdefault(source, set()).add(target)
        self.reverse.setdefault(target, set()).add(source)

    @staticmethod
    def _module_of(raw_import: str) -> str | None:
        match = _JS_FROM.search(raw_import) or _JS_REQUIRE.search(raw_import)
        if match:
            return match.group(1)
        match = _PY_FROM.match(raw_import) or _PY_IMPORT.match(raw_import)
        if match:
            return match.group(1)
        return None

    def _resolve(self, source_file: str, raw_import: str, file_set: set[str]) -> str | None:
        module = self._module_of(raw_import)
        if not module:
            return None
        if module.startswith("."):
            return self._resolve_relative(source_file, module, file_set)
        if raw_import.strip().startswith(("import ", "from ")) and "." in module and not module.startswith("."):
            return self._resolve_python_module(module, file_set)
        return None

    @staticmethod
    def _resolve_relative(source_file: str, module: str, file_set: set[str]) -> str | None:
        base = PurePosixPath(source_file).parent
        candidate = (base / module).as_posix()
        candidate = _re.sub(r"/(\./)+", "/", candidate)
        parts = []
        for part in candidate.split("/"):
            if part == "..":
                if parts:
                    parts.pop()
            elif part and part != ".":
                parts.append(part)
        normalized = "/".join(parts)
        if normalized in file_set:
            return normalized
        for suffix in _JS_EXTENSIONS:
            attempt = normalized + suffix if not suffix.startswith("/") else normalized + suffix
            if attempt in file_set:
                return attempt
        return None

    @staticmethod
    def _resolve_python_module(module: str, file_set: set[str]) -> str | None:
        as_path = module.replace(".", "/")
        for candidate in (f"{as_path}.py", f"{as_path}/__init__.py"):
            if candidate in file_set:
                return candidate
            for prefix in ("src/",):
                if f"{prefix}{candidate}" in file_set:
                    return f"{prefix}{candidate}"
        return None

    def dependencies_of(self, file: str) -> list[str]:
        return sorted(self.edges.get(file, set()))

    def impacted_by(self, file: str) -> list[str]:
        """Files likely impacted if `file` changes — direct importers, then
        their importers (depth 2), deduplicated."""
        direct = self.reverse.get(file, set())
        indirect: set[str] = set()
        for dependent in direct:
            indirect |= self.reverse.get(dependent, set())
        return sorted((direct | indirect) - {file})
