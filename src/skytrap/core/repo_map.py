import os
from pathlib import Path

from skytrap.core.context import WorkspaceContext
from skytrap.tools.filesystem import IGNORED_DIRS

MAX_ENTRIES = 300


def build_repo_map(workspace: WorkspaceContext, max_entries: int = MAX_ENTRIES) -> str:
    """A minimal file-tree overview of the workspace, so the model has basic
    orientation without needing a list_directory call for every question. This is
    deliberately just a directory/file listing — no AST, no dependency graph, no
    embeddings. Real repo intelligence (selecting only relevant context for large
    repos) comes later.
    """
    root = workspace.path
    lines: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
        rel_dir = Path(dirpath).relative_to(root)
        depth = len(rel_dir.parts) if rel_dir != Path(".") else 0

        if rel_dir != Path("."):
            lines.append(f"{'  ' * (depth - 1)}{rel_dir.name}/")
            if len(lines) >= max_entries:
                lines.append(f"... (truncated at {max_entries} entries)")
                return "\n".join(lines)

        for filename in sorted(filenames):
            lines.append(f"{'  ' * depth}{filename}")
            if len(lines) >= max_entries:
                lines.append(f"... (truncated at {max_entries} entries)")
                return "\n".join(lines)

    return "\n".join(lines) if lines else "(empty workspace)"
