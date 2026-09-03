"""Item 4 — SEMANTIC DUPLICATION DETECTOR.

`check_existence` (existence.py) answers "does *this specific name/path* exist".
`ExistingCapabilityDetector` answers a different question: "does something that
does what this description asks for already exist, possibly under a completely
different name?" — the gap the last pass left open: SkyTrap correctly refused to
re-"create" index.html, but could still propose `src/services/auth.ts` for "add
an authentication service" when `src/auth/authService.ts` already exists,
because nothing compared the *responsibility*, only the literal path.

Deliberately conservative: this only ever downgrades CREATE->REUSE/MODIFY when
there's real word-overlap evidence (matched symbols/files/routes), never blocks
a genuinely new file. Every result carries its evidence so a human/planner can
verify the claim, exactly like ExistenceEvidence.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from skytrap.core.context import WorkspaceContext
from skytrap.intelligence.existence import ExistenceEvidence, ExistenceStatus, check_existence
from skytrap.intelligence.graph import DependencyGraph
from skytrap.intelligence.snapshot import RepositorySnapshot
from skytrap.intelligence.symbols import SymbolIndex

_ROUTE_PATTERN = re.compile(
    r"""(?:\.(?:get|post|put|patch|delete)\s*\(\s*|@(?:app|router)\.(?:get|post|put|patch|delete)\s*\(\s*)"""
    r"""["']([^"']+)["']""",
    re.IGNORECASE,
)
_WORD_BOUNDARY = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")

_STOPWORDS = {
    "the", "a", "an", "to", "for", "of", "and", "or", "in", "on", "add", "create", "new",
    "service", "module", "file", "component", "function", "implement", "build", "make",
    # Generic programming keywords that appear in nearly every source file — these
    # would otherwise make the ripgrep-based fallback below match almost anything.
    "export", "import", "default", "return", "class", "interface", "public", "private",
    "async", "await", "const", "let", "var", "type", "extends", "implements",
}


def _words(text: str) -> set[str]:
    """Splits camelCase/PascalCase/snake_case/kebab-case/plain text into a
    lowercase word set — the shared vocabulary duplication detection compares
    across a natural-language description, a symbol name, and a file path."""
    raw = re.split(r"[_\-./\s]+", text)
    words: set[str] = set()
    for chunk in raw:
        for match in _WORD_BOUNDARY.finditer(chunk):
            word = match.group(0).lower()
            if len(word) >= 3 and word not in _STOPWORDS:
                words.add(word)
    return words


def _overlap_ratio(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a)


class ExistingCapabilityDetector:
    """`check()` is the entry point: given what a plan proposes to CREATE and why
    (the feature description), it looks for an existing symbol/file/route that
    already covers the same ground, under any name."""

    def __init__(
        self,
        *,
        symbol_index: SymbolIndex | None = None,
        dependency_graph: DependencyGraph | None = None,
    ) -> None:
        self.symbol_index = symbol_index
        self.dependency_graph = dependency_graph

    def check(
        self,
        workspace: WorkspaceContext,
        snapshot: RepositorySnapshot,
        *,
        description: str,
        proposed_path: str,
        min_overlap: float = 0.5,
    ) -> ExistenceEvidence:
        description_words = _words(description) | _words(PurePosixPath(proposed_path).stem)
        if not description_words:
            return ExistenceEvidence(
                query=description, status=ExistenceStatus.UNKNOWN,
                reason="no meaningful keywords extracted from the description",
            )

        matched_files: list[str] = []
        matched_symbols: list[str] = []
        best_ratio = 0.0

        # 1. Symbol names elsewhere in the repo (functions/classes/components).
        if self.symbol_index is not None:
            for name in self.symbol_index.all_names():
                ratio = _overlap_ratio(description_words, _words(name))
                if ratio >= min_overlap:
                    entries = [e for e in self.symbol_index.find(name) if e.file != proposed_path]
                    if entries:
                        matched_symbols.append(name)
                        best_ratio = max(best_ratio, ratio)

        # 2. Other files whose path shares enough vocabulary with the description
        #    ("src/auth/authService.ts" vs. "add an authentication service").
        for file in snapshot.files:
            if file == proposed_path:
                continue
            ratio = _overlap_ratio(description_words, _words(file))
            if ratio >= min_overlap:
                matched_files.append(file)
                best_ratio = max(best_ratio, ratio)

        # 3. Route strings (app.get("/auth/login"), @router.post("/auth")) — a
        #    different filename can still register the same HTTP surface.
        for file in snapshot.files:
            if file == proposed_path or PurePosixPath(file).suffix not in {".py", ".ts", ".js", ".tsx", ".jsx"}:
                continue
            try:
                text = (workspace.path / file).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for route in _ROUTE_PATTERN.findall(text):
                ratio = _overlap_ratio(description_words, _words(route))
                if ratio >= min_overlap:
                    matched_files.append(file)
                    best_ratio = max(best_ratio, ratio)

        if not matched_files and not matched_symbols:
            # Fall back to the same textual/ripgrep evidence check_existence uses —
            # but on the pre-filtered significant keywords, not the raw sentence.
            # The raw description is full of generic programming/English words
            # ("export", "add", "the") that would otherwise ripgrep-match nearly
            # any source file and produce a false duplicate.
            significant = " ".join(sorted(description_words))
            fallback = check_existence(workspace, snapshot, significant)
            fallback_files = [f for f in fallback.matched_files if f != proposed_path]
            if fallback.status in {ExistenceStatus.EXISTS, ExistenceStatus.PARTIAL} and fallback_files:
                return ExistenceEvidence(
                    query=description,
                    status=ExistenceStatus.PARTIAL,
                    matched_files=fallback_files,
                    reason="matched by textual search elsewhere in the repository — verify before creating a new file",
                )
            return ExistenceEvidence(
                query=description,
                status=ExistenceStatus.MISSING if not snapshot.truncated else ExistenceStatus.UNKNOWN,
                reason="no similarly-named symbol or file found elsewhere",
            )

        status = ExistenceStatus.EXISTS if best_ratio >= 0.8 else ExistenceStatus.PARTIAL
        return ExistenceEvidence(
            query=description,
            status=status,
            matched_files=matched_files[:10],
            matched_symbols=matched_symbols[:10],
            reason=(
                f"{'a symbol' if matched_symbols else 'a file'} elsewhere shares "
                f"{best_ratio:.0%} of the description's vocabulary — likely the same "
                "responsibility under a different name"
            ),
        )
