from skytrap.core.context import WorkspaceContext
from skytrap.core.languages.base import LanguageProfile, ResolvedCommands
from skytrap.core.languages.registry import register_language


def _resolve_ruby(workspace: WorkspaceContext) -> ResolvedCommands | None:
    root = workspace.path
    has_bundle = (root / "Gemfile").exists()
    prefix = "bundle exec " if has_bundle else ""
    is_rails = (root / "config" / "application.rb").exists() or (root / "bin" / "rails").exists()

    test_commands: tuple[str, ...]
    if is_rails:
        test_commands = (f"{prefix}rails test",)
    elif (root / "spec").is_dir():
        test_commands = (f"{prefix}rspec",)
    else:
        test_commands = ()

    return ResolvedCommands(
        test_commands=test_commands,
        lint_commands=(f"{prefix}rubocop",),
    )


ruby_profile = register_language(
    LanguageProfile(
        id="ruby",
        name="Ruby",
        extensions=(".rb",),
        manifests=("Gemfile", "*.gemspec"),
        package_managers=("gem", "bundler"),
        toolchain_executables=("ruby", "gem", "bundle", "rspec", "rubocop"),
        resolve_commands=_resolve_ruby,
        notes="Detects Rails (config/application.rb or bin/rails) to prefer `rails test` over rspec.",
    )
)
