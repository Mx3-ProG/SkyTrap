import asyncio
import threading

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from skytrap.server.ws.confirmation_bridge import ConfirmationBridge

router = APIRouter()

POLL_INTERVAL_SECONDS = 0.05


@router.websocket("/ws/confirm-demo")
async def confirm_demo(websocket: WebSocket) -> None:
    """Proves ConfirmationBridge works through a real WebSocket/asyncio round-trip,
    not just in isolation with a mocked send. Not the product: milestone 5 replaces
    this with the real endpoint that drives an actual run_agent_turn. On connect, a
    worker thread immediately requests one demo confirmation; the client is expected
    to answer it with {"type": "confirm_response", "id": ..., "answer": bool}, and
    receives {"type": "demo_result", "answer": bool} once the worker thread unblocks
    (by an answer, a timeout, or the socket disconnecting).
    """
    await websocket.accept()
    loop = asyncio.get_running_loop()

    async def send_to_client(message: dict) -> None:
        await websocket.send_json(message)

    bridge = ConfirmationBridge(send_to_client=send_to_client, loop=loop)

    result_holder: dict[str, bool] = {}
    done_event = threading.Event()

    def worker() -> None:
        result_holder["answer"] = bridge.request("Demo preview", "demo", timeout_seconds=30)
        done_event.set()

    threading.Thread(target=worker, daemon=True).start()

    try:
        # Only this coroutine ever calls websocket.send_json — the worker thread
        # only touches the bridge, never the socket directly, so there's no race
        # between a cross-thread send and this handler closing the connection.
        while not done_event.is_set():
            try:
                message = await asyncio.wait_for(
                    websocket.receive_json(), timeout=POLL_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                continue
            if message.get("type") == "confirm_response":
                bridge.resolve(message["id"], bool(message.get("answer")))

        await websocket.send_json({"type": "demo_result", "answer": result_holder["answer"]})
    except WebSocketDisconnect:
        bridge.abandon_all()
