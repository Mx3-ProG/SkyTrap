import io

from rich.console import Console

from skytrap.ui.terminal import (
    AgentRenderer,
    TerminalCapabilities,
    format_diff_body,
    render_diff_panel,
)


def capture_console(width=100):
    stream = io.StringIO()
    return stream, Console(file=stream, width=width, color_system=None, force_terminal=False)


ADD_ONLY_DIFF = "\n".join(
    [
        "--- a/f.py",
        "+++ b/f.py",
        "@@ -1,1 +1,2 @@",
        " keep",
        "+new line",
    ]
)

REMOVE_ONLY_DIFF = "\n".join(
    [
        "--- a/f.py",
        "+++ b/f.py",
        "@@ -1,2 +1,1 @@",
        " keep",
        "-old line",
    ]
)

MULTI_HUNK_DIFF = "\n".join(
    [
        "--- a/f.py",
        "+++ b/f.py",
        "@@ -1,2 +1,2 @@",
        "-top old",
        "+top new",
        " middle",
        "@@ -10,2 +10,2 @@",
        "-bottom old",
        "+bottom new",
        " tail",
    ]
)


def test_diff_body_shows_added_lines_in_green():
    caps = TerminalCapabilities(unicode=True, color=True)
    body, hidden = format_diff_body(ADD_ONLY_DIFF, caps)
    plain = body.plain
    assert "+new line" in plain
    assert hidden == 0
    spans = [s for s in body.spans if "new line" in plain[s.start : s.end]]
    assert any(span.style == "green" for span in spans)


def test_diff_body_shows_removed_lines_in_red():
    caps = TerminalCapabilities(unicode=True, color=True)
    body, hidden = format_diff_body(REMOVE_ONLY_DIFF, caps)
    plain = body.plain
    assert "-old line" in plain
    spans = [s for s in body.spans if "old line" in plain[s.start : s.end]]
    assert any(span.style == "red" for span in spans)


def test_diff_body_handles_multiple_hunks_independently():
    caps = TerminalCapabilities(unicode=True, color=False)
    body, _hidden = format_diff_body(MULTI_HUNK_DIFF, caps)
    plain = body.plain
    assert plain.count("@@ -1,2 +1,2 @@") == 1
    assert plain.count("@@ -10,2 +10,2 @@") == 1
    assert "-top old" in plain and "+top new" in plain
    assert "-bottom old" in plain and "+bottom new" in plain


def test_large_diff_collapses_unchanged_context_and_reports_hidden_count():
    context_lines = [f" context {i}" for i in range(40)]
    lines = ["--- a/big.py", "+++ b/big.py", "@@ -1,42 +1,42 @@", "-old top", "+new top"]
    lines.extend(context_lines)
    lines.extend(["-old bottom", "+new bottom"])
    diff_text = "\n".join(lines)

    caps = TerminalCapabilities(unicode=True, color=False)
    body, hidden = format_diff_body(diff_text, caps)
    plain = body.plain

    assert hidden > 0
    assert "unchanged lines hidden" in plain
    assert "-old top" in plain and "+new bottom" in plain
    # the bulk of the 40 context lines must not all be printed verbatim
    assert plain.count("context") < 40


def test_full_diff_flag_shows_everything_uncollapsed():
    context_lines = [f" context {i}" for i in range(40)]
    lines = ["--- a/big.py", "+++ b/big.py", "@@ -1,42 +1,42 @@", "-old top", "+new top"]
    lines.extend(context_lines)
    diff_text = "\n".join(lines)

    caps = TerminalCapabilities(unicode=True, color=False)
    body, hidden = format_diff_body(diff_text, caps, full=True)
    assert hidden == 0
    assert body.plain.count("context") == 40


def test_render_diff_panel_titles_with_the_file_path():
    caps = TerminalCapabilities(unicode=True, color=False)
    panel = render_diff_panel("src/auth/login.py", ADD_ONLY_DIFF, capabilities=caps)
    assert "PATCH src/auth/login.py" in str(panel.title)


def test_renderer_write_failure_never_shows_a_diff():
    stream, target = capture_console()
    caps = TerminalCapabilities(unicode=True, color=False, interactive=False)
    renderer = AgentRenderer(target_console=target, capabilities=caps)

    renderer.handle_event(
        {
            "kind": "tool_result",
            "tool": "write_file",
            "arguments": {"path": "broken.py"},
            "success": False,
            "metadata": {"diff": "+should not appear"},
        }
    )

    output = stream.getvalue()
    assert "should not appear" not in output
    assert "PATCH" not in output
    assert renderer.files == {}


def test_renderer_successful_write_renders_diff_and_file_counter():
    stream, target = capture_console()
    caps = TerminalCapabilities(unicode=True, color=False, interactive=False)
    renderer = AgentRenderer(target_console=target, capabilities=caps)

    renderer.handle_event(
        {
            "kind": "tool_result",
            "tool": "write_file",
            "arguments": {"path": "src/auth/login.py"},
            "success": True,
            "metadata": {
                "diff": ADD_ONLY_DIFF,
                "is_new_file": False,
                "added_lines": 1,
                "removed_lines": 0,
            },
        }
    )

    output = stream.getvalue()
    assert "PATCH src/auth/login.py" in output
    assert "+new line" in output
    assert "FILES 1 modified · 0 created · 0 deleted" in output
    assert renderer.files == {"src/auth/login.py": "modified"}
    assert renderer.additions == 1


def test_renderer_summary_reports_files_diff_stats_verification_and_checkpoint():
    stream, target = capture_console()
    caps = TerminalCapabilities(unicode=True, color=False, interactive=False)
    renderer = AgentRenderer(target_console=target, capabilities=caps)

    events = [
        {
            "kind": "tool_result",
            "tool": "write_file",
            "arguments": {"path": "new_module.py"},
            "success": True,
            "metadata": {"diff": ADD_ONLY_DIFF, "is_new_file": True, "added_lines": 40, "removed_lines": 0},
        },
        {
            "kind": "tool_result",
            "tool": "write_file",
            "arguments": {"path": "existing.py"},
            "success": True,
            "metadata": {"diff": REMOVE_ONLY_DIFF, "is_new_file": False, "added_lines": 2, "removed_lines": 17},
        },
        {
            "kind": "tool_result",
            "tool": "delete_file",
            "arguments": {"path": "old.py"},
            "success": True,
            "metadata": {"diff": REMOVE_ONLY_DIFF, "is_delete": True, "added_lines": 0, "removed_lines": 3},
        },
        {"kind": "verification_stage", "stage": "test", "success": True, "skipped": False},
        {"kind": "verification_stage", "stage": "build", "success": True, "skipped": False},
        {"kind": "checkpoint", "success": True, "metadata": {"checkpoint_commit": "8ac41ef1234", "diff": "full diff"}},
        {"kind": "task_completed", "task": {}},
    ]
    for event in events:
        renderer.handle_event(event)

    output = stream.getvalue()
    assert "TREASURE FOUND" in output
    assert "3 files touched  (1 modified · 1 created · 1 deleted)" in output
    assert "+42 additions" in output
    assert "-20 deletions" in output
    assert "Test" in output and "PASS" in output
    assert "Build" in output and "PASS" in output
    assert "8ac41ef1234"[:12] in output


def test_spinner_disabled_in_non_tty_prints_deterministic_log_line_and_never_threads():
    stream, target = capture_console()
    caps = TerminalCapabilities(unicode=True, color=False, interactive=False)
    renderer = AgentRenderer(target_console=target, capabilities=caps)

    renderer.start_activity("Exploring repository...")
    assert renderer._status is None
    assert renderer._ticker is None
    renderer.finish_activity()

    output = stream.getvalue()
    assert "Exploring repository..." in output
    assert output.count("\x1b[") == 0


def test_spinner_starts_and_stops_cleanly_when_interactive():
    stream, target = capture_console()
    caps = TerminalCapabilities(unicode=True, color=True, interactive=True)
    renderer = AgentRenderer(target_console=target, capabilities=caps)

    renderer.start_activity("Applying patch...")
    assert renderer._status is not None
    renderer.finish_activity()
    assert renderer._status is None
    assert renderer._stop_ticker is None


def test_ascii_fallback_diff_panel_has_no_unicode_box_or_symbols():
    caps = TerminalCapabilities(unicode=False, color=False)
    panel = render_diff_panel("f.py", ADD_ONLY_DIFF, capabilities=caps)
    stream, target = capture_console(width=60)
    target.print(panel)
    output = stream.getvalue()
    assert "╭" not in output and "╰" not in output
    assert "\x1b[" not in output


def test_no_color_env_diff_panel_has_no_ansi_codes(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    from skytrap.ui.terminal import detect_terminal_capabilities

    stream, target = capture_console()
    caps = detect_terminal_capabilities(target)
    assert caps.color is False

    panel = render_diff_panel("f.py", ADD_ONLY_DIFF, capabilities=caps)
    target.print(panel)
    output = stream.getvalue()
    assert "\x1b[" not in output


def test_ci_env_disables_interactive_animation(monkeypatch):
    monkeypatch.setenv("CI", "true")
    from skytrap.ui.terminal import detect_terminal_capabilities

    _stream, target = capture_console()
    caps = detect_terminal_capabilities(target)
    assert caps.interactive is False


def test_long_running_activity_shows_dynamic_duration_after_threshold():
    stream, target = capture_console()
    caps = TerminalCapabilities(unicode=True, color=False, interactive=True)
    renderer = AgentRenderer(target_console=target, capabilities=caps)

    import time as _time

    renderer._activity_label = "Running: npm test"
    renderer._activity_started_at = _time.monotonic()
    text_now = renderer._activity_text()
    assert text_now.plain == "Running: npm test"

    # Duration suffix only appears once elapsed crosses the 3s threshold — simulate
    # that by faking a start time far enough in the past.
    renderer._activity_started_at = _time.monotonic() - 12.4
    text_later = renderer._activity_text()
    assert "Running: npm test ·" in text_later.plain
    assert "s" in text_later.plain
    renderer.finish_activity()


def test_activity_label_from_structured_tool_call_event():
    caps = TerminalCapabilities(unicode=True, color=False, interactive=False)
    renderer = AgentRenderer(capabilities=caps, target_console=Console(file=io.StringIO(), width=80))

    label = renderer._label_for_activity_event(
        {"kind": "activity", "phase": "tool_call", "tool": "read_file", "arguments": {"path": "a.py"}}
    )
    assert label == "Reading a.py..."

    label = renderer._label_for_activity_event({"kind": "activity", "phase": "planning"})
    assert label == "Planning next move..."

    label = renderer._label_for_activity_event({"kind": "activity", "phase": "verification", "stage": "lint"})
    assert label == "Running lint..."

    label = renderer._label_for_activity_event({"kind": "activity", "phase": "checkpoint"})
    assert label == "Creating checkpoint..."
