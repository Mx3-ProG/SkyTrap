"""Item 1 — pre-flight repository analysis.

Builds a `RepositorySnapshot`: real, checkable evidence about what already
exists in the workspace. This is deliberately richer than a file-name listing
(`skytrap.core.repo_map.build_repo_map`) — a bare tree is exactly what let
SkyTrap claim it "created index.html" when the file was sitting right there,
because nothing forced the model to look for it specifically. Every mutating
autonomous task now runs this first and hands the result to the planner as
"Existing evidence".
"""

from __future__ import annotations

import json
import os
import subprocess
import re
from pathlib import Path

from pydantic import BaseModel, Field

from skytrap.core.context import WorkspaceContext
from skytrap.core.project_inspection import inspect_project
from skytrap.intelligence.conventions import ConventionProfile, detect_conventions
from skytrap.intelligence.parser import CodeParser
from skytrap.tools.filesystem import IGNORED_DIRS

MAX_SNAPSHOT_FILES = 3000

ENTRYPOINT_CANDIDATES = (
    "index.html",
    "public/index.html",
    "src/main.tsx",
    "src/main.ts",
    "src/main.jsx",
    "src/main.js",
    "src/index.tsx",
    "src/index.ts",
    "src/index.jsx",
    "src/index.js",
    "src/App.tsx",
    "src/App.jsx",
    "src/app.py",
    "main.py",
    "app.py",
    "manage.py",
    "wsgi.py",
    "asgi.py",
    "pages/index.tsx",
    "pages/index.jsx",
    "pages/index.js",
    "app/page.tsx",
)

MANIFEST_FILES = (
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "Pipfile",
    "Cargo.toml",
    "go.mod",
    "composer.json",
    "Gemfile",
    "pom.xml",
    "build.gradle",
)

LOCKFILE_TO_PACKAGE_MANAGER = {
    "package-lock.json": "npm",
    "yarn.lock": "yarn",
    "pnpm-lock.yaml": "pnpm",
    "bun.lockb": "bun",
    "poetry.lock": "poetry",
    "uv.lock": "uv",
    "Pipfile.lock": "pipenv",
    "Cargo.lock": "cargo",
    "go.sum": "go modules",
    "composer.lock": "composer",
}

CONFIG_BASENAME_HINTS = (
    "tsconfig",
    "vite.config",
    "webpack.config",
    "next.config",
    "rollup.config",
    "esbuild.config",
    ".eslintrc",
    "eslint.config",
    "jest.config",
    "vitest.config",
    "babel.config",
    "tailwind.config",
    "pytest.ini",
    "setup.cfg",
    "ruff.toml",
    ".ruff",
    ".prettierrc",
)

BUILD_SYSTEM_HINTS = {
    "vite.config": "Vite",
    "webpack.config": "webpack",
    "rollup.config": "Rollup",
    "esbuild.config": "esbuild",
    "next.config": "Next.js",
    "Makefile": "make",
    "Cargo.toml": "cargo",
}

FRAMEWORK_DEPENDENCY_HINTS = {
    "react": "React",
    "react-dom": "React",
    "next": "Next.js",
    "vue": "Vue",
    "nuxt": "Nuxt",
    "svelte": "Svelte",
    "@sveltejs/kit": "SvelteKit",
    "express": "Express",
    "fastify": "Fastify",
    "@nestjs/core": "NestJS",
    "vite": "Vite",
}

PYTHON_FRAMEWORK_HINTS = {
    "django": "Django",
    "flask": "Flask",
    "fastapi": "FastAPI",
    "starlette": "Starlette",
    "pyramid": "Pyramid",
}

TEST_PATH_HINTS = ("tests/", "test/", "__tests__/", "spec/", "_test.", "test_", ".test.", ".spec.")


class GitState(BaseModel):
    is_git: bool = False
    branch: str | None = None
    head: str | None = None
    dirty: bool = False


class RepositorySnapshot(BaseModel):
    """Structured, real evidence about a workspace — never just a file list."""

    root: str
    fingerprint: str
    files: list[str] = Field(default_factory=list)
    truncated: bool = False
    directories: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    package_managers: list[str] = Field(default_factory=list)
    manifests: list[str] = Field(default_factory=list)
    entrypoints: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    configs: list[str] = Field(default_factory=list)
    build_system: list[str] = Field(default_factory=list)
    git: GitState = Field(default_factory=GitState)
    dependencies: dict[str, list[str]] = Field(default_factory=dict)
    modules: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    routes: list[str] = Field(default_factory=list)
    symbols: dict[str, list[str]] = Field(default_factory=dict)
    imports: dict[str, list[str]] = Field(default_factory=dict)
    exports: dict[str, list[str]] = Field(default_factory=dict)
    dependency_relationships: dict[str, list[str]] = Field(default_factory=dict)
    recent_files: list[str] = Field(default_factory=list)
    conventions: ConventionProfile = Field(default_factory=ConventionProfile)

    def has_file(self, relative_path: str) -> bool:
        return relative_path.replace("\\", "/").lstrip("./") in self._file_set()

    def find_by_basename(self, name: str) -> list[str]:
        target = name.lower()
        return [f for f in self.files if Path(f).name.lower() == target]

    def find_by_stem(self, stem: str) -> list[str]:
        target = stem.lower()
        return [f for f in self.files if Path(f).stem.lower() == target]

    def _file_set(self) -> set[str]:
        return set(self.files)

    def evidence_lines(self, limit: int = 12) -> list[str]:
        """Short human-readable bullets — exactly the "Existing evidence:" block
        the planner prompt is required to show (item 15)."""
        lines: list[str] = []
        if self.entrypoints:
            lines.append(f"Entrypoint(s): {', '.join(self.entrypoints[:3])}")
        if self.frameworks:
            lines.append(f"Framework(s): {', '.join(self.frameworks)}")
        if self.build_system:
            lines.append(f"Build system: {', '.join(self.build_system)}")
        if self.package_managers:
            lines.append(f"Package manager(s): {', '.join(self.package_managers)}")
        for hint in self.conventions.guidance():
            lines.append(hint)
        if self.git.is_git:
            lines.append(f"Git: branch {self.git.branch or '?'}, {'dirty' if self.git.dirty else 'clean'}")
        return lines[:limit]


def _run_git(args: list[str], cwd: Path) -> str | None:
    try:
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _git_state(workspace: WorkspaceContext) -> GitState:
    if not workspace.is_git:
        return GitState(is_git=False)
    head = _run_git(["rev-parse", "HEAD"], workspace.path)
    status = _run_git(["status", "--porcelain"], workspace.path)
    return GitState(
        is_git=True,
        branch=workspace.branch,
        head=head,
        dirty=bool(status),
    )


def _recent_files(workspace: WorkspaceContext, limit: int = 30) -> list[str]:
    if not workspace.is_git:
        return []
    output = _run_git(["log", "-n", "20", "--name-only", "--pretty=format:"], workspace.path)
    if not output:
        return []
    seen: list[str] = []
    for line in output.splitlines():
        line = line.strip()
        if line and line not in seen:
            seen.append(line)
        if len(seen) >= limit:
            break
    return seen


def _walk_files(root: Path, max_files: int) -> tuple[list[str], list[str], bool]:
    files: list[str] = []
    directories: list[str] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".git"))
        rel_dir = Path(dirpath).relative_to(root)
        if rel_dir != Path("."):
            directories.append(rel_dir.as_posix())
        for filename in sorted(filenames):
            rel_path = (rel_dir / filename).as_posix() if rel_dir != Path(".") else filename
            files.append(rel_path)
            if len(files) >= max_files:
                truncated = True
                return files, directories, truncated
    return files, directories, truncated


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def build_repository_snapshot(
    workspace: WorkspaceContext, *, max_files: int = MAX_SNAPSHOT_FILES
) -> RepositorySnapshot:
    root = workspace.path
    files, directories, truncated = _walk_files(root, max_files)
    file_set = set(files)

    manifests = [name for name in MANIFEST_FILES if name in file_set]
    package_managers = sorted(
        {pm for lockfile, pm in LOCKFILE_TO_PACKAGE_MANAGER.items() if lockfile in file_set}
    )
    if "package.json" in file_set and not package_managers:
        package_managers.append("npm")

    entrypoints = [candidate for candidate in ENTRYPOINT_CANDIDATES if candidate in file_set]
    tests = [f for f in files if any(hint in f for hint in TEST_PATH_HINTS)][:200]
    configs = [
        f for f in files if any(Path(f).name.startswith(hint) for hint in CONFIG_BASENAME_HINTS)
    ][:100]
    build_system = sorted(
        {label for basename, label in BUILD_SYSTEM_HINTS.items() if any(f.endswith(basename) or Path(f).name == basename for f in files)}
    )

    package_json = _load_json(root / "package.json") if "package.json" in file_set else None
    manifest_text = ""
    for manifest in ("pyproject.toml", "requirements.txt", "Pipfile"):
        if manifest in file_set:
            try:
                manifest_text += (root / manifest).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                pass

    dependencies: dict[str, list[str]] = {}
    frameworks: list[str] = []
    if package_json:
        deps = {**package_json.get("dependencies", {}), **package_json.get("devDependencies", {})}
        dependencies["javascript"] = sorted(deps.keys())[:80]
        for name, label in FRAMEWORK_DEPENDENCY_HINTS.items():
            if name in deps and label not in frameworks:
                frameworks.append(label)
    if manifest_text:
        py_deps = set()
        for line in manifest_text.splitlines():
            token = line.strip().split("=")[0].strip().strip('"').strip("'").split("[")[0].split(">=")[0].split("==")[0].strip()
            if token and token.replace("-", "").replace("_", "").isalnum() and len(token) < 40:
                py_deps.add(token.lower())
        dependencies["python"] = sorted(py_deps)[:80]
        for name, label in PYTHON_FRAMEWORK_HINTS.items():
            if name in py_deps and label not in frameworks:
                frameworks.append(label)

    profile = inspect_project(workspace)
    languages = [match.profile.name for match in profile.languages]

    conventions = detect_conventions(files, package_json=package_json, manifest_text=manifest_text)
    modules, components, routes, symbols, imports, exports = _parse_repository_structure(
        root, files
    )

    git = _git_state(workspace)
    fingerprint = git.head or "no-git"
    if git.dirty:
        fingerprint += "-dirty"

    return RepositorySnapshot(
        root=str(root),
        fingerprint=fingerprint,
        files=files,
        truncated=truncated,
        directories=directories,
        languages=languages,
        frameworks=frameworks,
        package_managers=package_managers,
        manifests=manifests,
        entrypoints=entrypoints,
        tests=tests,
        configs=configs,
        build_system=build_system,
        git=git,
        dependencies=dependencies,
        modules=modules,
        components=components,
        routes=routes,
        symbols=symbols,
        imports=imports,
        exports=exports,
        dependency_relationships=imports,
        recent_files=_recent_files(workspace),
        conventions=conventions,
    )


_ROUTE_PATTERN = re.compile(
    r"(?:@(?:app|router)\.(?:get|post|put|patch|delete)|(?:app|router)\.(?:get|post|put|patch|delete))\s*\(\s*[\"']([^\"']+)|"
    r"(?:path|route)\s*=\s*[\"']([^\"']+)",
    re.I,
)


def _parse_repository_structure(root: Path, files: list[str], max_files: int = 600):
    parser = CodeParser()
    modules: list[str] = []
    components: list[str] = []
    routes: list[str] = []
    symbols: dict[str, list[str]] = {}
    imports: dict[str, list[str]] = {}
    exports: dict[str, list[str]] = {}
    for relative in files:
        if len(modules) >= max_files:
            break
        if parser.language_for_path(relative) is None:
            continue
        parsed = parser.parse_file(root / relative, relative_path=relative)
        if parsed is None:
            continue
        modules.append(relative)
        if parsed.symbols:
            symbols[relative] = [f"{item.kind}:{item.name}" for item in parsed.symbols[:100]]
            components.extend(
                f"{relative}:{item.name}" for item in parsed.symbols if item.kind == "component"
            )
        if parsed.imports:
            imports[relative] = parsed.imports[:100]
        if parsed.exports:
            exports[relative] = parsed.exports[:100]
        try:
            source = (root / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in _ROUTE_PATTERN.finditer(source):
            route = match.group(1) or match.group(2)
            if route and route not in routes:
                routes.append(route)
    return modules, components[:300], routes[:300], symbols, imports, exports
