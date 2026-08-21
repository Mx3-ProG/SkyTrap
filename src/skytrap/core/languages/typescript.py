from skytrap.core.context import WorkspaceContext
from skytrap.core.languages.base import LanguageProfile, ResolvedCommands
from skytrap.core.languages.javascript import detect_node_package_manager, has_node_test_script
from skytrap.core.languages.registry import register_language


def _resolve_ts(workspace: WorkspaceContext) -> ResolvedCommands | None:
    manager = detect_node_package_manager(workspace)
    run = manager if manager != "npm" else "npm run"
    test_cmd = f"{manager} test" if has_node_test_script(workspace) else None
    return ResolvedCommands(
        check_command="tsc --noEmit",
        build_commands=(f"{run} build",),
        test_commands=(test_cmd,) if test_cmd else (),
        format_commands=(f"{run} format", "npx prettier --write ."),
        lint_commands=(f"{run} lint", "npx eslint ."),
    )


typescript_profile = register_language(
    LanguageProfile(
        id="typescript",
        name="TypeScript",
        extensions=(".ts", ".tsx"),
        manifests=("tsconfig.json",),
        package_managers=("npm", "pnpm", "yarn", "bun"),
        check_command="tsc --noEmit",
        toolchain_executables=("node", "tsc", "npm", "pnpm", "yarn", "bun", "eslint", "prettier"),
        resolve_commands=_resolve_ts,
        notes="`tsc --noEmit` is the fast check — a real build only when the task needs output.",
    )
)
