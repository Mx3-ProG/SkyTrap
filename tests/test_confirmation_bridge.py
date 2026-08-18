import asyncio
import threading
import time

import pytest

from skytrap.server.ws.confirmation_bridge import ConfirmationBridge


@pytest.fixture
def bridge_with_loop():
    """A real asyncio event loop running on its own thread — mirrors production,
    where FastAPI's event loop thread is distinct from the worker thread(s) running
    agent turns. sent_messages captures what the bridge tried to send, without
    needing a real WebSocket."""
    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
    loop_thread.start()

    sent_messages: list[dict] = []

    async def send_to_client(message: dict) -> None:
        sent_messages.append(message)

    bridge = ConfirmationBridge(send_to_client=send_to_client, loop=loop)

    yield bridge, sent_messages

    loop.call_soon_threadsafe(loop.stop)
    loop_thread.join(timeout=2)


def test_normal_response_unblocks_the_worker_thread(bridge_with_loop):
    bridge, sent_messages = bridge_with_loop
    results = []

    def worker():
        results.append(bridge.request("Apply this write?", "write_file", timeout_seconds=5))

    worker_thread = threading.Thread(target=worker)
    worker_thread.start()

    # wait for the request to actually reach the "client" before resolving it
    deadline = time.monotonic() + 2
    while not sent_messages and time.monotonic() < deadline:
        time.sleep(0.01)
    assert sent_messages, "confirm_request was never sent"

    request_id = sent_messages[0]["id"]
    assert sent_messages[0]["kind"] == "write_file"
    assert sent_messages[0]["preview"] == "Apply this write?"

    resolved = bridge.resolve(request_id, True)
    worker_thread.join(timeout=2)

    assert resolved is True
    assert results == [True]


def test_decline_response_returns_false(bridge_with_loop):
    bridge, sent_messages = bridge_with_loop
    results = []

    def worker():
        results.append(bridge.request("Run this command?", "shell", timeout_seconds=5))

    worker_thread = threading.Thread(target=worker)
    worker_thread.start()

    deadline = time.monotonic() + 2
    while not sent_messages and time.monotonic() < deadline:
        time.sleep(0.01)

    bridge.resolve(sent_messages[0]["id"], False)
    worker_thread.join(timeout=2)

    assert results == [False]


def test_timeout_resolves_to_false_without_hanging(bridge_with_loop):
    bridge, _ = bridge_with_loop
    start = time.monotonic()

    result = bridge.request("Never answered", "write_file", timeout_seconds=0.2)

    elapsed = time.monotonic() - start
    assert result is False
    assert elapsed < 1.0  # actually returned promptly, not stuck


def test_abandon_all_unblocks_pending_worker_immediately(bridge_with_loop):
    bridge, sent_messages = bridge_with_loop
    results = []

    def worker():
        # a long timeout — abandon_all() must unblock it well before this elapses
        results.append(bridge.request("Pending forever?", "write_file", timeout_seconds=60))

    worker_thread = threading.Thread(target=worker)
    worker_thread.start()

    deadline = time.monotonic() + 2
    while not sent_messages and time.monotonic() < deadline:
        time.sleep(0.01)

    start = time.monotonic()
    bridge.abandon_all()
    worker_thread.join(timeout=2)
    elapsed = time.monotonic() - start

    assert results == [False]
    assert elapsed < 1.0  # unblocked immediately, did not wait out the 60s timeout


def test_stale_reply_after_timeout_is_ignored_not_raised(bridge_with_loop):
    bridge, sent_messages = bridge_with_loop

    result = bridge.request("Times out", "write_file", timeout_seconds=0.1)
    assert result is False

    # the request_id was already popped by the timeout path — a late reply must be
    # a harmless no-op, not an exception, and must not affect anything else
    stale_id = sent_messages[0]["id"]
    resolved = bridge.resolve(stale_id, True)
    assert resolved is False


def test_resolve_with_unknown_request_id_is_a_safe_no_op(bridge_with_loop):
    bridge, _ = bridge_with_loop
    assert bridge.resolve("never-existed", True) is False


def test_concurrent_confirmations_do_not_cross_wires(bridge_with_loop):
    bridge, sent_messages = bridge_with_loop
    results: dict[str, bool] = {}
    lock = threading.Lock()

    def worker(name: str, expected_answer: bool):
        result = bridge.request(f"Confirmation for {name}", "write_file", timeout_seconds=5)
        with lock:
            results[name] = result

    threads = [
        threading.Thread(target=worker, args=("alice", True)),
        threading.Thread(target=worker, args=("bob", False)),
        threading.Thread(target=worker, args=("carol", True)),
    ]
    for t in threads:
        t.start()

    deadline = time.monotonic() + 2
    while len(sent_messages) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(sent_messages) == 3

    answers = {"alice": True, "bob": False, "carol": True}
    for message in sent_messages:
        preview = message["preview"]
        name = preview.rsplit(" ", 1)[-1]
        bridge.resolve(message["id"], answers[name])

    for t in threads:
        t.join(timeout=2)

    assert results == answers
