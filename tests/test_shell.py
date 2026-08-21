from skytrap.core.context import WorkspaceContext
from skytrap.tools.shell import ShellTool, classify_command


def test_safe_commands():
    assert classify_command("ls") == "SAFE"
    assert classify_command("git status") == "SAFE"
    assert classify_command("pytest") == "SAFE"


def test_confirm_commands():
    assert classify_command("npm install") == "CONFIRM"
    assert classify_command("git commit -m x") == "CONFIRM"


def test_destructive_commands():
    assert classify_command("rm somefile.txt") == "DESTRUCTIVE"
    assert classify_command("mv a.txt b.txt") == "DESTRUCTIVE"
    assert classify_command("git reset --hard") == "DESTRUCTIVE"
    assert classify_command("git push") == "DESTRUCTIVE"
    assert classify_command("git checkout -- file.txt") == "DESTRUCTIVE"


def test_unknown_command_defaults_to_confirm():
    assert classify_command("some_random_binary --flag") == "CONFIRM"


def test_forbidden_commands():
    assert classify_command("sudo rm -rf /") == "FORBIDDEN"
    assert classify_command("shutdown -h now") == "FORBIDDEN"
    assert classify_command("diskutil eraseDisk") == "FORBIDDEN"


def _workspace(tmp_path):
    return WorkspaceContext(path=tmp_path, name=tmp_path.name, is_git=False)


def test_shell_destructive_command_uses_confirm_destructive_not_regular_confirm(tmp_path):
    calls = {"confirm": 0, "confirm_destructive": 0}

    tool = ShellTool(
        confirm=lambda preview: calls.__setitem__("confirm", calls["confirm"] + 1) or True,
        confirm_destructive=lambda preview: calls.__setitem__(
            "confirm_destructive", calls["confirm_destructive"] + 1
        )
        or False,
    )
    result = tool.execute(_workspace(tmp_path), {"command": "rm somefile.txt"})

    assert result.success is False
    assert result.output == "User declined to run this command."
    assert calls == {"confirm": 0, "confirm_destructive": 1}


def test_shell_confirm_command_uses_regular_confirm(tmp_path):
    calls = {"confirm": 0, "confirm_destructive": 0}

    tool = ShellTool(
        confirm=lambda preview: calls.__setitem__("confirm", calls["confirm"] + 1) or False,
        confirm_destructive=lambda preview: calls.__setitem__(
            "confirm_destructive", calls["confirm_destructive"] + 1
        )
        or True,
    )
    result = tool.execute(_workspace(tmp_path), {"command": "npm install"})

    assert result.success is False
    assert calls == {"confirm": 1, "confirm_destructive": 0}


def test_shell_safe_command_never_confirms(tmp_path):
    tool = ShellTool(confirm=lambda preview: (_ for _ in ()).throw(AssertionError("should not confirm")))
    result = tool.execute(_workspace(tmp_path), {"command": "echo hi"})

    assert result.success is True
    assert "hi" in result.output
