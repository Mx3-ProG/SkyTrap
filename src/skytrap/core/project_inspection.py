from dataclasses import dataclass

from skytrap.core.context import WorkspaceContext
from skytrap.core.language_detection import detect_languages
from skytrap.core.languages import LanguageMatch, ResolvedCommands
from skytrap.core.toolchain import KNOWN_EXECUTABLES, detect_toolchain


@dataclass
class ProjectProfile:
    languages: list[LanguageMatch]
    toolchain: dict[str, str | None]

    @property
    def primary_language(self) -> LanguageMatch | None:
        return self.languages[0] if self.languages else None


def inspect_project(workspace: WorkspaceContext) -> ProjectProfile:
    """The one place that ties language detection + toolchain discovery together —
    called at the start of a chat session/build so both the terminal and the
    system prompt reflect what's actually in the repository, not an assumption."""
    languages = detect_languages(workspace)
    relevant_executables = {exe for match in languages for exe in match.profile.toolchain_executables}
    toolchain = detect_toolchain(tuple(relevant_executables) or KNOWN_EXECUTABLES)
    return ProjectProfile(languages=languages, toolchain=toolchain)


def resolve_commands(workspace: WorkspaceContext, match: LanguageMatch) -> ResolvedCommands:
    """The concrete commands to actually run for this language in this workspace —
    calls the profile's resolve_commands() (lockfile/build-system-aware) when it has
    one, otherwise falls back to its static fields."""
    profile = match.profile
    if profile.resolve_commands is not None:
        resolved = profile.resolve_commands(workspace)
        if resolved is not None:
            return resolved
    return ResolvedCommands(
        check_command=profile.check_command,
        build_commands=profile.build_commands,
        test_commands=profile.test_commands,
        format_commands=profile.format_commands,
        lint_commands=profile.lint_commands,
    )
