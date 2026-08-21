from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from skytrap.core.projects import ProjectStore
from skytrap.models.base import ModelProvider
from skytrap.server.auth.email import load_email_sender
from skytrap.server.auth.router import router as auth_router
from skytrap.server.auth.store import AuthStore
from skytrap.server.config import Settings, load_settings
from skytrap.server.routers.projects import router as projects_router
from skytrap.server.routers.turns import router as turns_router
from skytrap.server.turns import TurnRegistry
from skytrap.server.ws.connection import ConnectionManager
from skytrap.server.ws.router import router as ws_router

# Only the Vite dev server origin for now — this is a local-first, single-user
# system; once a real remote deployment (Tailscale/Vercel) is in play, this list
# grows to include that origin instead of ever using allow_origins=["*"].
DEV_FRONTEND_ORIGINS = ["http://localhost:5173"]

FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"


def create_app(
    settings: Settings | None = None,
    auth_store: AuthStore | None = None,
    model_provider: ModelProvider | None = None,
    project_store: ProjectStore | None = None,
) -> FastAPI:
    """Assembles shared dependencies (settings, the auth store, the email sender,
    the connection manager, the turn registry, and optionally the model provider)
    into app.state once at startup, so route handlers don't each need to know how
    to construct them — the same RegistryContext-style pattern already used for
    tools/skills. `settings`/`auth_store` are overridable so tests can inject an
    isolated store instead of the real ~/.skytrap/skytrap.db; `model_provider`
    lets tests inject a FakeModelProvider instead of hitting the real Ollama
    server for every turn.
    """
    app = FastAPI(title="SkyTrap")
    app.state.settings = settings or load_settings()
    app.state.auth_store = auth_store or AuthStore()
    app.state.email_sender = load_email_sender()
    app.state.connection_manager = ConnectionManager()
    app.state.turn_registry = TurnRegistry()
    app.state.model_provider = model_provider
    app.state.project_store = project_store or ProjectStore()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=DEV_FRONTEND_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth_router)
    app.include_router(turns_router)
    app.include_router(projects_router)
    app.include_router(ws_router)

    # Serves the built PWA (npm run build in frontend/) once it exists. Mounted
    # last and at "/" so it only ever catches paths none of the API routers
    # above matched — registration order is what FastAPI/Starlette use to
    # resolve routes, so this must stay below every app.include_router() call.
    # Guarded by existence so environments (including the test suite) that
    # never built the frontend don't fail to start.
    if FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

    return app


app = create_app()
