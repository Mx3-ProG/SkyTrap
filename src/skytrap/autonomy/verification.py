from __future__ import annotations

import shlex
import shutil
import subprocess
import json
import os
from pathlib import Path
from enum import StrEnum
from time import monotonic

from pydantic import BaseModel, Field

from skytrap.core.context import WorkspaceContext
from skytrap.core.project_inspection import inspect_project, resolve_commands
from skytrap.tools.base import ToolResult


class VerificationStage(StrEnum):
    LINT = "lint"
    TYPECHECK = "typecheck"
    TEST = "test"
    BUILD = "build"


class VerificationResult(BaseModel):
    success: bool
    results: list[ToolResult] = Field(default_factory=list)
    failed_stage: VerificationStage | None = None
    skipped_stages: list[VerificationStage] = Field(default_factory=list)


class VerificationLoop:
    def __init__(self, timeout_seconds: int = 300, max_output_chars: int = 12_000):
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars

    def discover(self, workspace: WorkspaceContext) -> dict[VerificationStage, list[str]]:
        discovered = {stage: [] for stage in VerificationStage}
        for match in inspect_project(workspace).languages:
            commands = resolve_commands(workspace, match)
            for command in commands.lint_commands:
                stage = VerificationStage.TYPECHECK if self._is_typecheck(command) else VerificationStage.LINT
                discovered[stage].append(command)
            if commands.check_command:
                stage = VerificationStage.TYPECHECK if self._is_typecheck(commands.check_command) else VerificationStage.LINT
                discovered[stage].append(commands.check_command)
            discovered[VerificationStage.TEST].extend(commands.test_commands)
            discovered[VerificationStage.BUILD].extend(commands.build_commands)
        result = {
            stage: [
                command
                for command in dict.fromkeys(filter(None, commands))
                if self._command_is_configured(workspace.path, command)
            ]
            for stage, commands in discovered.items()
        }
        if not any(result.values()):
            # Item 6 — VERIFICATION FALLBACK ENGINE. Primary discovery is driven by
            # manifest "scripts" (package.json) / language-profile commands, and
            # finds nothing on e.g. a Vite/TS project with no npm scripts defined —
            # the exact "no verification command discovered" failure mode. Only
            # tried when discovery found literally nothing, from a small known
            # registry, each candidate still gated by the same real-tool-presence
            # check (_command_is_configured) as every other verification command.
            result = self._discover_fallback(workspace)
        return result

    def _discover_fallback(self, workspace: WorkspaceContext) -> dict[VerificationStage, list[str]]:
        root = workspace.path
        candidates: dict[VerificationStage, list[str]] = {stage: [] for stage in VerificationStage}

        if (root / "tsconfig.json").is_file():
            candidates[VerificationStage.TYPECHECK].append("tsc --noEmit")
        if self._vite_project(root):
            candidates[VerificationStage.BUILD].append("vite build")
        if self._python_tests_present(root):
            candidates[VerificationStage.TEST].append("pytest")
        if any(root.rglob("*.py")):
            if (
                (root / "ruff.toml").is_file()
                or (root / ".ruff.toml").is_file()
                or self._pyproject_mentions(root, "ruff")
            ):
                candidates[VerificationStage.LINT].append("ruff check .")
            if (root / "mypy.ini").is_file() or self._pyproject_mentions(root, "mypy"):
                candidates[VerificationStage.TYPECHECK].append("mypy .")

        return {
            stage: [command for command in commands if self._command_is_configured(root, command)]
            for stage, commands in candidates.items()
        }

    @staticmethod
    def _vite_project(root: Path) -> bool:
        if any(root.glob("vite.config.*")):
            return True
        try:
            package = json.loads((root / "package.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        deps = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
        return "vite" in deps

    @staticmethod
    def _python_tests_present(root: Path) -> bool:
        return (
            any(root.rglob("test_*.py"))
            or any(root.rglob("*_test.py"))
            or (root / "tests").is_dir()
        )

    @staticmethod
    def _pyproject_mentions(root: Path, tool: str) -> bool:
        try:
            text = (root / "pyproject.toml").read_text(encoding="utf-8").lower()
        except OSError:
            return False
        return tool in text

    @staticmethod
    def _package_scripts(root: Path) -> tuple[dict, set[str]]:
        try:
            package = json.loads((root / "package.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}, set()
        dependencies = set(package.get("dependencies", {})) | set(package.get("devDependencies", {}))
        return package.get("scripts", {}), dependencies

    @classmethod
    def _command_is_configured(cls, root: Path, command: str) -> bool:
        try:
            tokens = shlex.split(command)
        except ValueError:
            return False
        if not tokens:
            return False
        local_binary = root / "node_modules" / ".bin" / tokens[0]
        venv_binary = root / ".venv" / "bin" / tokens[0]
        if shutil.which(tokens[0]) is None and not local_binary.exists() and not venv_binary.exists():
            return False

        scripts, dependencies = cls._package_scripts(root)
        managers = {"npm", "pnpm", "yarn", "bun"}
        if tokens[0] in managers:
            if tokens[0] == "npm" and len(tokens) >= 3 and tokens[1] == "run":
                return tokens[2] in scripts
            if len(tokens) >= 2 and tokens[1] in {"test", "build", "lint", "typecheck"}:
                return tokens[1] in scripts
            if len(tokens) >= 3 and tokens[1] == "run":
                return tokens[2] in scripts

        if tokens[0] == "npx" and len(tokens) >= 2:
            binary = tokens[1]
            return binary in dependencies or (root / "node_modules" / ".bin" / binary).exists()

        if tokens[0] == "uv" and len(tokens) >= 3 and tokens[1] == "run":
            tool = tokens[2]
            try:
                pyproject = (root / "pyproject.toml").read_text(encoding="utf-8").lower()
            except OSError:
                pyproject = ""
            return tool.lower() in pyproject or (root / ".venv" / "bin" / tool).exists()

        optional_tools = {"ruff", "mypy", "pyright", "golangci-lint", "clang-tidy", "rubocop", "tsc", "eslint"}
        if tokens[0] in optional_tools:
            return shutil.which(tokens[0]) is not None or (root / "node_modules" / ".bin" / tokens[0]).exists()

        if tokens[:2] == ["make", "test"]:
            try:
                return "test:" in (root / "Makefile").read_text(encoding="utf-8")
            except OSError:
                return False
        return True

    @staticmethod
    def _is_typecheck(command: str) -> bool:
        lowered = command.lower()
        return any(marker in lowered for marker in ("mypy", "pyright", "typecheck", "tsc", "cargo check"))

    def run(
        self,
        workspace: WorkspaceContext,
        commands: dict[VerificationStage, list[str]] | None = None,
    ) -> VerificationResult:
        selected = commands if commands is not None else self.discover(workspace)
        results: list[ToolResult] = []
        skipped: list[VerificationStage] = []
        for stage in VerificationStage:
            stage_commands = selected.get(stage, [])
            if not stage_commands:
                skipped.append(stage)
                continue
            for command in stage_commands:
                result = self._run_command(workspace, stage, command)
                results.append(result)
                if not result.success:
                    return VerificationResult(
                        success=False,
                        results=results,
                        failed_stage=stage,
                        skipped_stages=skipped,
                    )
        return VerificationResult(success=bool(results), results=results, skipped_stages=skipped)

    def _run_command(
        self, workspace: WorkspaceContext, stage: VerificationStage, command: str
    ) -> ToolResult:
        try:
            tokens = shlex.split(command)
        except ValueError as exc:
            return ToolResult(success=False, output=f"Could not parse verification command: {exc}", stderr=str(exc))
        if shutil.which(tokens[0]) is None:
            for candidate in (
                workspace.path / "node_modules" / ".bin" / tokens[0],
                workspace.path / ".venv" / "bin" / tokens[0],
            ):
                if candidate.exists():
                    tokens[0] = str(candidate)
                    break

        expanded: list[str] = []
        for token in tokens:
            if any(marker in token for marker in ("*", "?", "[")):
                matches = [str(path.relative_to(workspace.path)) for path in workspace.path.glob(token)]
                expanded.extend(matches or [token])
            else:
                expanded.append(token)
        tokens = expanded
        started = monotonic()
        try:
            environment = os.environ.copy()
            environment.setdefault("PYTHONDONTWRITEBYTECODE", "1")
            completed = subprocess.run(
                tokens,
                cwd=workspace.path,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env=environment,
            )
        except FileNotFoundError:
            return ToolResult(success=False, output=f"Command not found: {tokens[0]}", stderr=f"Command not found: {tokens[0]}")
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, output=f"Verification timed out: {command}", stderr="timeout")
        output = "\n".join(part for part in (completed.stdout.strip(), completed.stderr.strip()) if part)
        output = output[-self.max_output_chars :]
        return ToolResult(
            success=completed.returncode == 0,
            output=output or f"{stage.value} passed: {command}",
            stdout=completed.stdout[-self.max_output_chars :],
            stderr=completed.stderr[-self.max_output_chars :],
            exit_code=completed.returncode,
            metadata={
                "stage": stage.value,
                "command": command,
                "duration_ms": round((monotonic() - started) * 1000, 2),
            },
        )
