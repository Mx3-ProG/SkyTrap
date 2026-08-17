from skytrap.tools.shell import classify_command


def test_safe_commands():
    assert classify_command("ls") == "SAFE"
    assert classify_command("git status") == "SAFE"
    assert classify_command("pytest") == "SAFE"


def test_confirm_commands():
    assert classify_command("npm install") == "CONFIRM"
    assert classify_command("git commit -m x") == "CONFIRM"
    assert classify_command("rm somefile.txt") == "CONFIRM"


def test_unknown_command_defaults_to_confirm():
    assert classify_command("some_random_binary --flag") == "CONFIRM"


def test_forbidden_commands():
    assert classify_command("sudo rm -rf /") == "FORBIDDEN"
    assert classify_command("shutdown -h now") == "FORBIDDEN"
    assert classify_command("diskutil eraseDisk") == "FORBIDDEN"
