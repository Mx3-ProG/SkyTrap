from fastapi import FastAPI

from skytrap.server.auth.email import load_email_sender
from skytrap.server.auth.router import router as auth_router
from skytrap.server.auth.store import AuthStore
from skytrap.server.config import Settings, load_settings
from skytrap.server.ws.demo_router import router as ws_demo_router


def create_app(settings: Settings | None = None, auth_store: AuthStore | None = None) -> FastAPI:
    """Assembles shared dependencies (settings, the auth store, the email sender)
    into app.state once at startup, so route handlers don't each need to know how
    to construct them — the same RegistryContext-style pattern already used for
    tools/skills. `settings`/`auth_store` are overridable so tests can inject an
    isolated store instead of the real ~/.skytrap/skytrap.db.
    """
    app = FastAPI(title="SkyTrap")
    app.state.settings = settings or load_settings()
    app.state.auth_store = auth_store or AuthStore()
    app.state.email_sender = load_email_sender()

    app.include_router(auth_router)
    # Temporary test/demo scaffolding for the confirmation bridge — no auth on this
    # one on purpose (proving the mechanism, not shipping a feature); replaced by
    # the real turns/WS endpoint in the next milestone.
    app.include_router(ws_demo_router)

    return app


app = create_app()
