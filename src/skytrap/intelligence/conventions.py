"""Item 12 — architecture/convention detection.

Detects patterns the repository already follows (folder layout for a given
kind of module, naming casing, test framework) so the planner is told to
follow them instead of inventing a new one. Deliberately simple, deterministic
frequency counting — no ML, no guessing beyond what's actually on disk.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import PurePosixPath

from pydantic import BaseModel, Field

_SERVICE_LIKE_DIRS = ("services", "lib", "utils", "helpers", "clients", "api")
_COMPONENT_LIKE_DIRS = ("components", "pages", "views", "screens", "widgets")
_TEST_DIR_HINTS = ("tests", "test", "__tests__", "spec")

_CAMEL = re.compile(r"^[a-z][a-zA-Z0-9]*$")
_PASCAL = re.compile(r"^[A-Z][a-zA-Z0-9]*$")
_SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")
_KEBAB = re.compile(r"^[a-z][a-z0-9-]*$")


class ConventionProfile(BaseModel):
    service_module_dir: str | None = None
    component_dir: str | None = None
    test_dir: str | None = None
    naming_style: str | None = None  # "camelCase" | "PascalCase" | "snake_case" | "kebab-case"
    test_framework: str | None = None
    lint_tools: list[str] = Field(default_factory=list)
    state_management: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def guidance(self) -> list[str]:
        """Short imperative sentences meant to go straight into a planner prompt."""
        lines: list[str] = []
        if self.service_module_dir:
            lines.append(
                f'This project already places service/utility modules under "{self.service_module_dir}/" '
                "— put new ones there instead of inventing a new top-level directory."
            )
        if self.component_dir:
            lines.append(f'UI components live under "{self.component_dir}/" — follow that layout.')
        if self.test_dir:
            lines.append(f'Tests live under "{self.test_dir}/" — add new tests there, matching existing naming.')
        if self.naming_style:
            lines.append(f"This codebase's dominant file naming style is {self.naming_style} — match it.")
        if self.test_framework:
            lines.append(f"The existing test framework is {self.test_framework} — use it, don't introduce another.")
        if self.lint_tools:
            lines.append(f"Existing lint/format tooling: {', '.join(self.lint_tools)} — stay compatible with it.")
        if self.state_management:
            lines.append(f"Existing state management: {', '.join(self.state_management)} — reuse it.")
        return lines


def _dominant_dir(files: list[str], candidates: tuple[str, ...]) -> str | None:
    counts: Counter[str] = Counter()
    for file in files:
        parts = PurePosixPath(file).parts
        for part in parts[:-1]:
            if part.lower() in candidates:
                counts[part] += 1
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def _naming_style(basenames: list[str]) -> str | None:
    votes: Counter[str] = Counter()
    for name in basenames:
        stem = PurePosixPath(name).stem
        if not stem or stem.startswith("."):
            continue
        if _PASCAL.match(stem):
            votes["PascalCase"] += 1
        elif _CAMEL.match(stem):
            votes["camelCase"] += 1
        elif _KEBAB.match(stem) and "-" in stem:
            votes["kebab-case"] += 1
        elif _SNAKE.match(stem):
            votes["snake_case"] += 1
    if not votes:
        return None
    style, count = votes.most_common(1)[0]
    return style if count >= 3 else None


def detect_conventions(
    files: list[str],
    *,
    package_json: dict | None = None,
    manifest_text: str = "",
) -> ConventionProfile:
    source_files = [
        f
        for f in files
        if PurePosixPath(f).suffix in {".py", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte"}
    ]
    profile = ConventionProfile(
        service_module_dir=_dominant_dir(source_files, _SERVICE_LIKE_DIRS),
        component_dir=_dominant_dir(source_files, _COMPONENT_LIKE_DIRS),
        test_dir=_dominant_dir(files, _TEST_DIR_HINTS),
        naming_style=_naming_style([PurePosixPath(f).name for f in source_files]),
    )

    lint_tools: list[str] = []
    state_management: list[str] = []
    dependencies: dict = {}
    if package_json:
        dependencies = {
            **package_json.get("dependencies", {}),
            **package_json.get("devDependencies", {}),
        }
    for name, tool in (("eslint", "ESLint"), ("prettier", "Prettier"), ("ruff", "Ruff"), ("black", "Black")):
        if name in dependencies or re.search(rf"\b{name}\b", manifest_text, re.I):
            lint_tools.append(tool)
    for name, label in (
        ("redux", "Redux"),
        ("zustand", "Zustand"),
        ("recoil", "Recoil"),
        ("jotai", "Jotai"),
        ("pinia", "Pinia"),
        ("vuex", "Vuex"),
    ):
        if name in dependencies:
            state_management.append(label)
    profile.lint_tools = lint_tools
    profile.state_management = state_management

    if "vitest" in dependencies:
        profile.test_framework = "Vitest"
    elif "jest" in dependencies:
        profile.test_framework = "Jest"
    elif any(PurePosixPath(f).name in {"pytest.ini", "conftest.py"} for f in files) or "pytest" in manifest_text:
        profile.test_framework = "pytest"
    elif any(f.startswith("__tests__/") or "/__tests__/" in f for f in files):
        profile.test_framework = "Jest"

    return profile
