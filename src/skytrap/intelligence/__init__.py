"""Repository Intelligence / Code Intelligence layer.

This package exists to enforce SkyTrap's core operating principle:

    READ -> UNDERSTAND -> PROVE -> PLAN -> PATCH -> VERIFY

...never PROMPT -> GENERATE FILES. A directory listing of file names is
deliberately never treated as sufficient understanding of a repository —
every module here builds real, checkable evidence (parsed symbols, existence
checks backed by an actual filesystem/ripgrep/AST search, a dependency graph)
that the planner and agent loop are required to consult before creating or
overwriting anything.
"""

from skytrap.intelligence.context_builder import BuiltContext, ContextBuilder
from skytrap.intelligence.conventions import ConventionProfile, detect_conventions
from skytrap.intelligence.duplication import ExistingCapabilityDetector
from skytrap.intelligence.existence import ExistenceEvidence, ExistenceStatus, check_existence
from skytrap.intelligence.graph import DependencyGraph
from skytrap.intelligence.parser import CodeParser, ParsedFile, Symbol
from skytrap.intelligence.policy import ENGINEERING_POLICY, policy_prompt
from skytrap.intelligence.repository_memory import RepositoryMemory, RepositoryMemoryStore
from skytrap.intelligence.snapshot import RepositorySnapshot, build_repository_snapshot
from skytrap.intelligence.structural_search import StructuralMatch, StructuralSearch
from skytrap.intelligence.symbols import SymbolEntry, SymbolIndex

__all__ = [
    "BuiltContext",
    "ContextBuilder",
    "ConventionProfile",
    "detect_conventions",
    "ExistingCapabilityDetector",
    "ExistenceEvidence",
    "ExistenceStatus",
    "check_existence",
    "DependencyGraph",
    "CodeParser",
    "ParsedFile",
    "Symbol",
    "ENGINEERING_POLICY",
    "policy_prompt",
    "RepositoryMemory",
    "RepositoryMemoryStore",
    "RepositorySnapshot",
    "build_repository_snapshot",
    "StructuralMatch",
    "StructuralSearch",
    "SymbolEntry",
    "SymbolIndex",
]
