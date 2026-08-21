from skytrap.core.context import WorkspaceContext
from skytrap.core.languages.base import LanguageProfile, ResolvedCommands
from skytrap.core.languages.registry import register_language

# Ordered by specificity — checked in this order so a repo with multiple lockfiles
# (e.g. a stale package-lock.json left after switching to pnpm) still picks the
# most likely intended manager.
_LOCKFILE_MANAGERS = (
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("bun.lockb", "bun"),
    ("bun.lock", "bun"),
    ("package-lock.json", "npm"),
)


def detect_node_package_manager(workspace: WorkspaceContext) -> str:
    """Never `npm install` a pnpm/yarn/bun project — the lockfile is the ground
    truth for which manager this repo actually uses. Defaults to npm when no
    lockfile exists yet (a brand new project)."""
    for lockfile, manager in _LOCKFILE_MANAGERS:
        if (workspace.path / lockfile).exists():
            return manager
    return "npm"


def has_node_test_script(workspace: WorkspaceContext) -> bool:
    import json

    package_json = workspace.path / "package.json"
    if not package_json.exists():
        return False
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return "test" in data.get("scripts", {})


def _resolve_js(workspace: WorkspaceContext) -> ResolvedCommands | None:
    manager = detect_node_package_manager(workspace)
    run = manager if manager != "npm" else "npm run"
    test_cmd = f"{manager} test" if has_node_test_script(workspace) else None
    return ResolvedCommands(
        build_commands=(f"{run} build",),
        test_commands=(test_cmd,) if test_cmd else (),
        format_commands=(f"{run} format", "npx prettier --write ."),
        lint_commands=(f"{run} lint", "npx eslint ."),
    )


javascript_profile = register_language(
    LanguageProfile(
        id="javascript",
        name="JavaScript",
        extensions=(".js", ".jsx", ".mjs", ".cjs"),
        manifests=("package.json",),
        package_managers=("npm", "pnpm", "yarn", "bun"),
        toolchain_executables=("node", "npm", "pnpm", "yarn", "bun", "eslint", "prettier"),
        resolve_commands=_resolve_js,
        notes="Package manager is picked from the lockfile (pnpm-lock.yaml/yarn.lock/bun.lock/"
        "package-lock.json), never assumed to be npm.",
    )
)
