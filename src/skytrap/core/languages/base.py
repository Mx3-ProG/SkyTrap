from dataclasses import dataclass
from typing import Callable

from skytrap.core.context import WorkspaceContext


@dataclass(frozen=True)
class LanguageProfile:
    """Everything SkyTrap needs to know to work in one language: how to recognize
    it, and which real, project-appropriate commands to run for each phase of work.
    A language only counts as "supported" once it has a profile here — never just
    because the model can produce text in that syntax (see core/languages/README
    principle: detect, read, edit, run/build, validate, interpret errors)."""

    id: str
    name: str
    # File extensions that count toward this language's share of the repo (with the dot).
    extensions: tuple[str, ...]
    # Filenames (or glob-like suffixes, e.g. "*.csproj") whose presence strongly
    # indicates this language/build system, independent of file-extension counts.
    manifests: tuple[str, ...]
    package_managers: tuple[str, ...] = ()
    # Fast, non-executing sanity check (e.g. `cargo check`) — cheaper than a full build.
    check_command: str | None = None
    build_commands: tuple[str, ...] = ()
    test_commands: tuple[str, ...] = ()
    format_commands: tuple[str, ...] = ()
    lint_commands: tuple[str, ...] = ()
    # Toolchain executables relevant to this language, for `skytrap toolchain`/the
    # project-detected panel — not all need to be present.
    toolchain_executables: tuple[str, ...] = ()
    # Overrides command selection based on the workspace (e.g. picking pnpm vs npm
    # from a lockfile). Returns None to fall through to the static fields above.
    resolve_commands: Callable[[WorkspaceContext], "ResolvedCommands | None"] | None = None
    notes: str = ""


@dataclass(frozen=True)
class ResolvedCommands:
    """What `resolve_commands` returns when a profile needs to pick concrete
    commands based on the actual workspace (e.g. the lockfile present)."""

    check_command: str | None = None
    build_commands: tuple[str, ...] = ()
    test_commands: tuple[str, ...] = ()
    format_commands: tuple[str, ...] = ()
    lint_commands: tuple[str, ...] = ()


@dataclass
class LanguageMatch:
    """One detected language in a repository, with how confidently it was detected."""

    profile: LanguageProfile
    file_count: int
    percentage: float
    manifest_detected: bool
