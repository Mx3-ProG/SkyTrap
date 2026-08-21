from skytrap.core.context import WorkspaceContext
from skytrap.core.project_inspection import inspect_project, resolve_commands
from skytrap.tools.base import Tool, ToolResult


class InspectProjectTool(Tool):
    name = "inspect_project"
    description = (
        "Detect which programming language(s) this workspace uses (by file "
        "extensions and manifest files like Cargo.toml/pyproject.toml/go.mod/"
        "*.csproj/Gemfile/package.json/CMakeLists.txt), which of their toolchains "
        "are actually installed on this machine, and the project-appropriate "
        "check/build/test/format/lint commands for each. Call this before writing "
        "code in an unfamiliar or multi-language repository so any commands you run "
        "match the project's real toolchain instead of a guess. No arguments."
    )

    def execute(self, workspace: WorkspaceContext, arguments: dict) -> ToolResult:
        profile = inspect_project(workspace)
        if not profile.languages:
            return ToolResult(success=True, output="No recognized language detected in this workspace.")

        lines = ["Languages detected:"]
        for match in profile.languages:
            marker = " (manifest found)" if match.manifest_detected else ""
            lines.append(f"- {match.profile.name}: {match.percentage}% of source files{marker}")

            commands = resolve_commands(workspace, match)
            for label, values in (
                ("check", (commands.check_command,) if commands.check_command else ()),
                ("build", commands.build_commands),
                ("test", commands.test_commands),
                ("format", commands.format_commands),
                ("lint", commands.lint_commands),
            ):
                if values:
                    lines.append(f"    {label}: {' | '.join(v for v in values if v)}")

        lines.append("")
        lines.append("Toolchain availability:")
        for name, path in sorted(profile.toolchain.items()):
            lines.append(f"- {name}: {'found at ' + path if path else 'NOT INSTALLED'}")

        return ToolResult(success=True, output="\n".join(lines))
