"""Item 9 — ContextBuilder.

A task no longer gets `goal + repo_map` (a bare file tree) and calls that
context. The model's context is assembled from prioritized, real evidence —
the request, repo architecture, files/symbols/dependencies actually likely to
matter, targeted excerpts, constraints and local conventions — under an
explicit token budget. Lower-priority sections are dropped first when the
budget is tight; the *entire* repository is never injected.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from skytrap.core.context import WorkspaceContext
from skytrap.intelligence.existence import ExistenceEvidence
from skytrap.intelligence.graph import DependencyGraph
from skytrap.intelligence.snapshot import RepositorySnapshot
from skytrap.intelligence.symbols import SymbolIndex

CHARS_PER_TOKEN = 4
DEFAULT_TOKEN_BUDGET = 6000
MAX_LIKELY_FILES = 6
MAX_EXCERPT_CHARS = 1200


class ContextSection(BaseModel):
    title: str
    content: str
    priority: int  # lower = kept first when the budget is tight


class BuiltContext(BaseModel):
    sections: list[ContextSection] = Field(default_factory=list)
    token_budget: int
    estimated_tokens: int = 0
    dropped_sections: list[str] = Field(default_factory=list)

    def render(self) -> str:
        return "\n\n".join(f"## {section.title}\n{section.content}" for section in self.sections if section.content.strip())


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def _read_excerpt(workspace: WorkspaceContext, relative_path: str, max_chars: int = MAX_EXCERPT_CHARS) -> str | None:
    try:
        text = (workspace.path / relative_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if len(text) > max_chars:
        return text[:max_chars] + f"\n... ({len(text) - max_chars} more characters truncated)"
    return text


class ContextBuilder:
    def build(
        self,
        workspace: WorkspaceContext,
        *,
        goal: str,
        snapshot: RepositorySnapshot,
        symbol_index: SymbolIndex | None = None,
        dependency_graph: DependencyGraph | None = None,
        existence_evidence: list[ExistenceEvidence] | None = None,
        recent_decisions: list[str] | None = None,
        constraints: list[str] | None = None,
        diagnostics: list[str] | None = None,
        previous_errors: list[str] | None = None,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
        expansion_level: int = 0,
    ) -> BuiltContext:
        existence_evidence = existence_evidence or []
        likely_files = self._likely_files(snapshot, existence_evidence)

        sections: list[ContextSection] = [
            ContextSection(title="Request", content=goal, priority=0),
            ContextSection(title="Existing evidence", content=self._evidence_section(snapshot, existence_evidence), priority=1),
            ContextSection(title="Diagnostics", content="\n".join((diagnostics or [])[-20:]) or "(none)", priority=1),
            ContextSection(title="Likely relevant files", content="\n".join(likely_files) or "(none identified yet)", priority=2),
            ContextSection(title="Relevant tests", content="\n".join(snapshot.tests[:20]) or "(none detected)", priority=4),
            ContextSection(title="Relevant symbols", content=self._symbols_section(symbol_index, likely_files), priority=4),
            ContextSection(title="Dependencies", content=self._dependencies_section(dependency_graph, likely_files), priority=3),
            ContextSection(title="Repository architecture", content=self._architecture_section(snapshot), priority=5),
            ContextSection(title="Targeted excerpts", content=self._excerpts_section(workspace, likely_files), priority=6),
            ContextSection(
                title="Recent decisions",
                content="\n".join(f"- {d}" for d in (recent_decisions or [])) or "(none yet)",
                priority=7,
            ),
            ContextSection(title="Previous errors", content="\n".join((previous_errors or [])[-10:]) or "(none)", priority=7),
            ContextSection(
                title="Constraints",
                content="\n".join(f"- {c}" for c in (constraints or [])) or "(none)",
                priority=8,
            ),
            ContextSection(title="Local conventions", content=self._conventions_section(snapshot), priority=9),
        ]

        sections.sort(key=lambda s: s.priority)
        kept: list[ContextSection] = []
        dropped: list[str] = []
        used = 0
        for section in sections:
            cost = _estimate_tokens(section.content)
            if used + cost > token_budget and section.priority != 0:
                dropped.append(section.title)
                continue
            kept.append(section)
            used += cost

        return BuiltContext(sections=kept, token_budget=token_budget, estimated_tokens=used, dropped_sections=dropped)

    def expand(self, workspace: WorkspaceContext, **kwargs) -> BuiltContext:
        """Progressively widen a prior inspection without ever dropping the budget."""
        level = int(kwargs.pop("expansion_level", 0)) + 1
        budget = int(kwargs.pop("token_budget", DEFAULT_TOKEN_BUDGET))
        return self.build(
            workspace,
            token_budget=min(budget * (level + 1), 32000),
            expansion_level=level,
            **kwargs,
        )

    @staticmethod
    def _likely_files(snapshot: RepositorySnapshot, evidence: list[ExistenceEvidence]) -> list[str]:
        files: list[str] = []
        for item in evidence:
            files.extend(item.matched_files)
        files.extend(snapshot.entrypoints)
        return list(dict.fromkeys(files))[:MAX_LIKELY_FILES]

    @staticmethod
    def _evidence_section(snapshot: RepositorySnapshot, evidence: list[ExistenceEvidence]) -> str:
        lines = list(snapshot.evidence_lines())
        lines.extend(item.as_bullet() for item in evidence)
        return "\n".join(f"- {line}" for line in lines) or "(no specific evidence gathered yet)"

    @staticmethod
    def _architecture_section(snapshot: RepositorySnapshot) -> str:
        lines = [
            f"Languages: {', '.join(snapshot.languages) or 'unknown'}",
            f"Frameworks: {', '.join(snapshot.frameworks) or 'none detected'}",
            f"Package manager(s): {', '.join(snapshot.package_managers) or 'none detected'}",
            f"Build system: {', '.join(snapshot.build_system) or 'none detected'}",
            f"Manifests: {', '.join(snapshot.manifests) or 'none'}",
            f"Entrypoints: {', '.join(snapshot.entrypoints) or 'none detected'}",
            f"Files indexed: {len(snapshot.files)}{' (truncated)' if snapshot.truncated else ''}",
        ]
        return "\n".join(lines)

    @staticmethod
    def _symbols_section(symbol_index: SymbolIndex | None, likely_files: list[str]) -> str:
        if symbol_index is None:
            return "(symbol index not built for this task)"
        lines = []
        for file in likely_files:
            parsed = symbol_index.parsed_file(file)
            if not parsed:
                continue
            names = ", ".join(f"{s.kind}:{s.name}" for s in parsed.symbols[:15])
            if names:
                lines.append(f"{file}: {names}")
        return "\n".join(lines) or "(no indexed symbols in the likely-relevant files)"

    @staticmethod
    def _dependencies_section(graph: DependencyGraph | None, likely_files: list[str]) -> str:
        if graph is None:
            return "(dependency graph not built for this task)"
        lines = []
        for file in likely_files:
            deps = graph.dependencies_of(file)
            impacted = graph.impacted_by(file)
            if deps or impacted:
                lines.append(f"{file}: depends on [{', '.join(deps) or '-'}]; impacts [{', '.join(impacted) or '-'}]")
        return "\n".join(lines) or "(no resolved dependency edges for the likely-relevant files)"

    @staticmethod
    def _excerpts_section(workspace: WorkspaceContext, likely_files: list[str]) -> str:
        parts = []
        for file in likely_files[:4]:
            excerpt = _read_excerpt(workspace, file)
            if excerpt is not None:
                parts.append(f"--- {file} ---\n{excerpt}")
        return "\n\n".join(parts) or "(no readable excerpt available yet)"

    @staticmethod
    def _conventions_section(snapshot: RepositorySnapshot) -> str:
        lines = snapshot.conventions.guidance()
        return "\n".join(f"- {line}" for line in lines) or "(no strong existing convention detected)"
