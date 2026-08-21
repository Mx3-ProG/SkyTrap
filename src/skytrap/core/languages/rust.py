from skytrap.core.languages.base import LanguageProfile
from skytrap.core.languages.registry import register_language

rust_profile = register_language(
    LanguageProfile(
        id="rust",
        name="Rust",
        extensions=(".rs",),
        manifests=("Cargo.toml", "Cargo.lock"),
        package_managers=("cargo",),
        # cargo check is much faster than a full build — prefer it for iterating.
        check_command="cargo check",
        build_commands=("cargo build",),
        test_commands=("cargo test",),
        format_commands=("cargo fmt",),
        lint_commands=("cargo clippy", "cargo fmt --check"),
        toolchain_executables=("cargo", "rustc"),
        notes="Prefer `cargo check` while iterating; `cargo fmt --check` + `cargo clippy` + "
        "`cargo test` are the final validation pass, not run on every single edit.",
    )
)
