import os

from skytrap.core.context import WorkspaceContext
from skytrap.core.languages import LanguageMatch, all_profiles
from skytrap.tools.filesystem import IGNORED_DIRS

MAX_FILES_SCANNED = 20_000


def _manifest_present(root, manifest: str) -> bool:
    """Exact filename ("Cargo.toml") or a glob pattern ("*.csproj") — checked at the
    workspace root and one level down, since manifests can live in a project
    subdirectory of a monorepo without being at the true repo root."""
    if "*" in manifest:
        if any(root.glob(manifest)):
            return True
        return any(root.glob(f"*/{manifest}"))
    if (root / manifest).exists():
        return True
    return any((root / d / manifest).exists() for d in os.listdir(root) if (root / d).is_dir())


def detect_languages(workspace: WorkspaceContext) -> list[LanguageMatch]:
    """Real detection, not a guess: counts source files by extension (skipping
    node_modules/.venv/build/etc via the same IGNORED_DIRS list every other tool
    tool uses) and separately checks for each language's manifest files anywhere in
    the top two directory levels — covers both single-language repos and
    `frontend/`, `api/`, `native/`-style monorepos. A language shows up in the
    result if either signal fires."""
    root = workspace.path
    extension_counts: dict[str, int] = {}
    total_files = 0

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        for filename in filenames:
            total_files += 1
            if total_files > MAX_FILES_SCANNED:
                break
            suffix = os.path.splitext(filename)[1]
            if suffix:
                extension_counts[suffix] = extension_counts.get(suffix, 0) + 1
        if total_files > MAX_FILES_SCANNED:
            break

    source_file_total = sum(extension_counts.values()) or 1

    matches: list[LanguageMatch] = []
    for profile in all_profiles():
        file_count = sum(extension_counts.get(ext, 0) for ext in profile.extensions)
        manifest_detected = any(_manifest_present(root, m) for m in profile.manifests)
        if file_count == 0 and not manifest_detected:
            continue
        matches.append(
            LanguageMatch(
                profile=profile,
                file_count=file_count,
                percentage=round(100 * file_count / source_file_total, 1),
                manifest_detected=manifest_detected,
            )
        )

    matches.sort(key=lambda m: (m.manifest_detected, m.file_count), reverse=True)
    return matches
