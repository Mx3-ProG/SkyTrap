"""Item 2 — existence check.

The Planner/Coder must never say "I will create X" purely because it hasn't
found X yet. UNKNOWN is a distinct, real state from MISSING: it means
inspection was incomplete (snapshot truncated, ripgrep unavailable, ambiguous
name) and SkyTrap must keep looking before creating anything.
"""

from __future__ import annotations

import re
import subprocess
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from skytrap.core.context import WorkspaceContext
from skytrap.intelligence.snapshot import RepositorySnapshot


class ExistenceStatus(StrEnum):
    EXISTS = "exists"
    PARTIAL = "partial"
    MISSING = "missing"
    UNKNOWN = "unknown"


class ExistenceEvidence(BaseModel):
    query: str
    status: ExistenceStatus
    matched_files: list[str] = Field(default_factory=list)
    matched_symbols: list[str] = Field(default_factory=list)
    text_matches: list[str] = Field(default_factory=list)
    reason: str = ""

    def as_bullet(self) -> str:
        if self.status == ExistenceStatus.EXISTS:
            where = self.matched_files[0] if self.matched_files else (self.matched_symbols[0] if self.matched_symbols else "")
            return f'"{self.query}" EXISTS' + (f" — {where}" if where else "")
        if self.status == ExistenceStatus.PARTIAL:
            return f'"{self.query}" PARTIALLY implemented ({self.reason})'
        if self.status == ExistenceStatus.MISSING:
            return f'"{self.query}" appears MISSING ({self.reason})' if self.reason else f'"{self.query}" appears MISSING'
        return f'"{self.query}" is UNKNOWN — inspection was inconclusive ({self.reason}); keep investigating before creating anything'


_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")


def _ripgrep_available() -> bool:
    try:
        subprocess.run(["rg", "--version"], capture_output=True, timeout=3)
        return True
    except (FileNotFoundError, subprocess.SubprocessError):
        return False


def _ripgrep_search(workspace: WorkspaceContext, query: str, *, max_matches: int = 20) -> list[str] | None:
    """Returns matched "file:line:content" strings, or None if ripgrep itself
    could not run (a real UNKNOWN condition, not a MISSING one)."""
    try:
        result = subprocess.run(
            ["rg", "--line-number", "--no-heading", "--color=never", "-i", "-m", str(max_matches), query, "."],
            cwd=workspace.path,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if result.returncode not in (0, 1):
        return None
    return result.stdout.splitlines()[:max_matches]


def _path_candidate(query: str) -> str | None:
    stripped = query.strip().strip("`'\"")
    if "/" in stripped or "." in stripped.split("/")[-1]:
        return stripped
    return None


def check_existence(
    workspace: WorkspaceContext,
    snapshot: RepositorySnapshot,
    query: str,
    *,
    symbol_hint: list[str] | None = None,
) -> ExistenceEvidence:
    """Multi-stage check, cheapest/most certain first:

    1. Exact relative path on disk (deterministic — never UNKNOWN).
    2. Basename/stem match anywhere in the snapshot's file list.
    3. ripgrep textual search for the query and its significant words.
    4. Fall back to UNKNOWN when the snapshot was truncated or ripgrep is
       unavailable and nothing else resolved the question — never silently
       MISSING when inspection itself was incomplete.
    """
    query = query.strip()
    if not query:
        return ExistenceEvidence(query=query, status=ExistenceStatus.UNKNOWN, reason="empty query")

    path_like = _path_candidate(query)
    if path_like:
        ok, resolved = _safe_resolve(workspace, path_like)
        if ok and Path(resolved).exists():
            return ExistenceEvidence(query=query, status=ExistenceStatus.EXISTS, matched_files=[path_like])
        basename_matches = snapshot.find_by_basename(Path(path_like).name)
        if basename_matches:
            return ExistenceEvidence(
                query=query,
                status=ExistenceStatus.EXISTS,
                matched_files=basename_matches,
                reason="matched by filename elsewhere in the repository",
            )

    name_matches = snapshot.find_by_stem(query) or [
        f for f in snapshot.files if query.lower() in Path(f).stem.lower()
    ]
    if name_matches:
        return ExistenceEvidence(query=query, status=ExistenceStatus.EXISTS, matched_files=name_matches[:10])

    words = [w for w in dict.fromkeys(_WORD.findall(query)) if len(w) >= 4]
    # A feature request rarely uses the exact identifier the codebase does
    # ("authentication" vs. AuthProvider/session.ts) — a shared word root
    # (first 5 chars) catches that without pretending to be a real synonym
    # dictionary. This only ever *widens* what counts as evidence to look at;
    # the final EXISTS/PARTIAL/MISSING call still requires an actual hit.
    stems = list(dict.fromkeys(w[:4] for w in words if len(w) > 6))
    path_signal_terms = list(dict.fromkeys([query, *words, *stems]))

    stem_file_matches = [
        f for f in snapshot.files if any(term.lower() in f.lower() for term in path_signal_terms if len(term) >= 4)
    ]
    if stem_file_matches:
        return ExistenceEvidence(
            query=query,
            status=ExistenceStatus.PARTIAL,
            matched_files=stem_file_matches[:10],
            reason="matched by a related keyword in the file path, not an exact name — verify before assuming full coverage",
        )

    if symbol_hint:
        found = [
            name
            for name in symbol_hint
            if name.lower() == query.lower()
            or any(term.lower() in name.lower() for term in path_signal_terms if len(term) >= 4)
        ]
        if found:
            return ExistenceEvidence(
                query=query,
                status=ExistenceStatus.PARTIAL,
                matched_symbols=found[:10],
                reason="matched by a related symbol name — verify before assuming full coverage",
            )

    if not _ripgrep_available():
        return ExistenceEvidence(
            query=query,
            status=ExistenceStatus.UNKNOWN,
            reason="ripgrep is unavailable — textual search could not be performed",
        )

    search_terms = list(dict.fromkeys([query, *words, *stems]))
    hits: list[str] = []
    for term in search_terms[:4]:
        found = _ripgrep_search(workspace, re.escape(term))
        if found is None:
            return ExistenceEvidence(
                query=query,
                status=ExistenceStatus.UNKNOWN,
                reason="ripgrep search failed unexpectedly",
            )
        hits.extend(found)

    if not hits:
        if snapshot.truncated:
            return ExistenceEvidence(
                query=query,
                status=ExistenceStatus.UNKNOWN,
                reason="repository snapshot was truncated — a matching file may not have been indexed",
            )
        return ExistenceEvidence(
            query=query,
            status=ExistenceStatus.MISSING,
            reason="no matching file name and no textual match found",
        )

    matched_terms = sum(1 for term in search_terms[:4] if any(term.lower() in hit.lower() for hit in hits))
    status = ExistenceStatus.EXISTS if matched_terms >= max(1, len(search_terms[:4]) - 1) else ExistenceStatus.PARTIAL
    matched_files = list(dict.fromkeys(hit.split(":", 1)[0] for hit in hits))[:10]
    return ExistenceEvidence(
        query=query,
        status=status,
        matched_files=matched_files,
        text_matches=hits[:10],
        reason="" if status == ExistenceStatus.EXISTS else "only some related terms were found — likely a partial implementation",
    )


def _safe_resolve(workspace: WorkspaceContext, relative_path: str) -> tuple[bool, str]:
    resolved = (workspace.path / relative_path).resolve()
    try:
        resolved.relative_to(workspace.path)
    except ValueError:
        return False, str(resolved)
    return True, str(resolved)
