from skytrap.core.languages.base import LanguageProfile

_PROFILES: dict[str, LanguageProfile] = {}


def register_language(profile: LanguageProfile) -> LanguageProfile:
    """Called once, at import time, by each language module (same pattern as
    tools/registry.py's @register_tool). A language file that's never imported by
    core/languages/__init__.py contributes nothing — that's the one place a new
    language needs to be wired in."""
    _PROFILES[profile.id] = profile
    return profile


def all_profiles() -> list[LanguageProfile]:
    return list(_PROFILES.values())


def get_profile(language_id: str) -> LanguageProfile | None:
    return _PROFILES.get(language_id)


def clear_registry() -> None:
    """Test-only: registration is global module state."""
    _PROFILES.clear()
