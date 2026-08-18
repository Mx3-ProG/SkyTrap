from fastapi import FastAPI

from skytrap.models.base import ModelProvider
from skytrap.server.auth.email import load_email_sender
from skytrap.server.auth.router import router as auth_router
from skytrap.server.auth.store import AuthStore
from skytrap.server.config import Settings, load_settings
from skytrap.server.routers.turns import router as turns_router
from skytrap.server.turns import TurnRegistry
from skytrap.server.ws.connection import ConnectionManager
from skytrap.server.ws.router import router as ws_router


def create_app(
    settings: Settings | None = None,
    auth_store: AuthStore | None = None,
    model_provider: ModelProvider | None = None,
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

    app.include_router(auth_router)
    app.include_router(turns_router)
    app.include_router(ws_router)

    return app


app = create_app()
