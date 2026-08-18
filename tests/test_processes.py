import sys
import time

import pytest

from skytrap.core import processes


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    """Points the process registry at a throwaway location so tests never touch the
    user's real ~/.skytrap/ directory or interfere with actually-tracked processes."""
    monkeypatch.setattr(processes, "DB_PATH", tmp_path / "skytrap.db")
    monkeypatch.setattr(processes, "LOG_DIR", tmp_path / "logs")


def _sleep_command(seconds: float) -> list[str]:
    # -u: unbuffered stdout, otherwise Python buffers output when not attached to a
    # tty and the log file stays empty until the buffer fills or the process exits.
    return [sys.executable, "-u", "-c", f"print('hello'); import time; time.sleep({seconds})"]


def test_start_process_tracks_a_running_process(tmp_path):
    record = processes.start_process(str(tmp_path), _sleep_command(2))

    assert record.id > 0
    assert processes.is_running(record.pid)
    assert record.running


def test_list_processes_includes_started_process(tmp_path):
    record = processes.start_process(str(tmp_path), _sleep_command(2))

    records = processes.list_processes()

    assert any(r.id == record.id and r.pid == record.pid for r in records)
    processes.stop_process(record.id)


def test_get_process_returns_none_for_unknown_id():
    assert processes.get_process(99999) is None


def test_stop_process_actually_terminates_it(tmp_path):
    record = processes.start_process(str(tmp_path), _sleep_command(10))
    assert processes.is_running(record.pid)

    ok, message = processes.stop_process(record.id)

    assert ok
    time.sleep(1)
    assert not processes.is_running(record.pid)
    assert not processes.get_process(record.id).running


def test_stop_process_unknown_id():
    ok, message = processes.stop_process(99999)
    assert not ok
    assert "No tracked process" in message


def test_tail_log_captures_process_output(tmp_path):
    record = processes.start_process(str(tmp_path), _sleep_command(1))
    time.sleep(0.5)

    output = processes.tail_log(record)

    assert "hello" in output
    processes.stop_process(record.id)
