from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from skytrap.core.context import detect_workspace
from skytrap.core.projects import Project, ProjectRegistrationError
from skytrap.core.tool_safety import classify_path
from skytrap.server.auth.dependencies import get_current_user_id
from skytrap.tools.filesystem import IGNORED_DIRS, MAX_READ_BYTES, resolve_in_workspace
from skytrap.tools.git import GitStatusTool
from skytrap.tools.shell import ShellTool

router = APIRouter(tags=["projects"])


def _project_or_404(request: Request, project_id: int) -> Project:
    project = request.app.state.project_store.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Unknown project")
    return project


def _workspace_for(project: Project):
    return detect_workspace(Path(project.path))


class RegisterProjectRequest(BaseModel):
    name: str
    path: str


@router.get("/projects")
def list_projects(request: Request, user_id: int = Depends(get_current_user_id)) -> list[dict]:
    return [vars(p) for p in request.app.state.project_store.list()]


@router.post("/projects")
def register_project(
    payload: RegisterProjectRequest, request: Request, user_id: int = Depends(get_current_user_id)
) -> dict:
    try:
        project = request.app.state.project_store.register(payload.name, payload.path)
    except ProjectRegistrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return vars(project)


@router.get("/projects/{project_id}")
def get_project(
    project_id: int, request: Request, user_id: int = Depends(get_current_user_id)
) -> dict:
    return vars(_project_or_404(request, project_id))


@router.delete("/projects/{project_id}")
def remove_project(
    project_id: int, request: Request, user_id: int = Depends(get_current_user_id)
) -> dict:
    _project_or_404(request, project_id)
    request.app.state.project_store.remove(project_id)
    return {"ok": True}


@router.get("/projects/{project_id}/files")
def list_files(
    project_id: int,
    request: Request,
    path: str = ".",
    user_id: int = Depends(get_current_user_id),
) -> list[dict]:
    project = _project_or_404(request, project_id)
    workspace = _workspace_for(project)
    ok, resolved = resolve_in_workspace(workspace, path)
    if not ok:
        raise HTTPException(status_code=400, detail=resolved)

    dir_path = Path(resolved)
    if not dir_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")

    entries = []
    for entry in sorted(dir_path.iterdir(), key=lambda e: (not e.is_dir(), e.name)):
        if entry.name in IGNORED_DIRS:
            continue
        entries.append({"name": entry.name, "is_dir": entry.is_dir()})
    return entries


@router.get("/projects/{project_id}/files/content")
def read_file_content(
    project_id: int,
    request: Request,
    path: str,
    user_id: int = Depends(get_current_user_id),
) -> dict:
    project = _project_or_404(request, project_id)
    workspace = _workspace_for(project)
    ok, resolved = resolve_in_workspace(workspace, path)
    if not ok:
        raise HTTPException(status_code=400, detail=resolved)

    file_path = Path(resolved)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if file_path.stat().st_size > MAX_READ_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large to open (> {MAX_READ_BYTES} bytes)")

    try:
        content = file_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=415, detail="Cannot open binary file") from exc
    return {"path": path, "content": content}


class WriteFileContentRequest(BaseModel):
    path: str
    content: str


@router.put("/projects/{project_id}/files/content")
def write_file_content(
    project_id: int,
    payload: WriteFileContentRequest,
    request: Request,
    user_id: int = Depends(get_current_user_id),
) -> dict:
    project = _project_or_404(request, project_id)
    workspace = _workspace_for(project)
    ok, resolved = resolve_in_workspace(workspace, payload.path)
    if not ok:
        raise HTTPException(status_code=400, detail=resolved)

    # No confirmation UI exists yet for saves made from the web editor (unlike the
    # CLI/turns pipeline, which has a live confirm bridge) — SAFE paths save
    # immediately, DESTRUCTIVE (secrets/credentials-shaped) paths are refused
    # rather than silently written or silently blocking ordinary files.
    if classify_path(payload.path) == "DESTRUCTIVE":
        raise HTTPException(
            status_code=403,
            detail="This path looks like a secret/credential file — edit it via the CLI, "
            "where a confirmation prompt is available.",
        )

    file_path = Path(resolved)
    if file_path.exists() and not file_path.is_file():
        raise HTTPException(status_code=400, detail=f"'{payload.path}' exists and is not a regular file")

    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(payload.content, encoding="utf-8")
    return {"path": payload.path, "bytes_written": len(payload.content)}


@router.get("/projects/{project_id}/git/status")
def git_status(
    project_id: int, request: Request, user_id: int = Depends(get_current_user_id)
) -> dict:
    project = _project_or_404(request, project_id)
    workspace = _workspace_for(project)
    result = GitStatusTool().execute(workspace, {})
    return {"success": result.success, "output": result.output}


class RunCommandRequest(BaseModel):
    command: str


@router.post("/projects/{project_id}/run")
def run_command(
    project_id: int,
    payload: RunCommandRequest,
    request: Request,
    user_id: int = Depends(get_current_user_id),
) -> dict:
    """Runs a shell command in the project directory. SAFE/CONFIRM-tier commands
    run immediately (this endpoint is already behind auth — equivalent to "Normal"
    permission mode); DESTRUCTIVE-tier commands (rm, git reset/push/checkout, mv)
    are refused rather than silently run, since there's no confirmation UI wired
    up for the web terminal yet (see docs/permissions.md)."""
    project = _project_or_404(request, project_id)
    workspace = _workspace_for(project)

    tool = ShellTool(confirm=lambda _preview: True, confirm_destructive=lambda _preview: False)
    result = tool.execute(workspace, {"command": payload.command})
    return {"success": result.success, "output": result.output}
