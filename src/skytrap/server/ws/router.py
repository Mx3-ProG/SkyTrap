import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from skytrap.server.auth.dependencies import ACCESS_COOKIE
from skytrap.server.auth.jwt import verify_access_token

router = APIRouter()

POLL_INTERVAL_SECONDS = 0.05


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    """The real streaming/confirmation channel, replacing /ws/confirm-demo. Auth
    uses the same access-token cookie as the HTTP routes (FastAPI's WebSocket
    exposes cookies the same way Request does) — no separate WS auth scheme.
    Becoming the active connection registers a fresh ConfirmationBridge with the
    app-wide ConnectionManager, which POST /turns and any in-flight turn's
    on_progress callback both read from.
    """
    token = websocket.cookies.get(ACCESS_COOKIE)
    secret_key = websocket.app.state.settings.secret_key
    user_id = verify_access_token(token, secret_key) if token else None
    if user_id is None:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    loop = asyncio.get_running_loop()
    connection_manager = websocket.app.state.connection_manager

    async def send_to_client(message: dict) -> None:
        await websocket.send_json(message)

    bridge = connection_manager.connect(send_to_client=send_to_client, loop=loop)

    try:
        while True:
            try:
                message = await asyncio.wait_for(
                    websocket.receive_json(), timeout=POLL_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                continue
            if message.get("type") == "confirm_response":
                bridge.resolve(message["id"], bool(message.get("answer")))
    except WebSocketDisconnect:
        pass
    finally:
        connection_manager.disconnect(bridge)
