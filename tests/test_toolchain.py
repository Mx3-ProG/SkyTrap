from skytrap.core.toolchain import detect_toolchain


def test_detect_toolchain_finds_git_which_must_exist_in_this_repo():
    result = detect_toolchain(("git",))
    assert result["git"] is not None


def test_detect_toolchain_reports_missing_executable_as_none():
    result = detect_toolchain(("definitely-not-a-real-executable-xyz",))
    assert result["definitely-not-a-real-executable-xyz"] is None
