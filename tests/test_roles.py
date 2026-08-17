from skytrap.core.roles import _looks_like_refusal


def test_detects_refusal_phrases():
    assert _looks_like_refusal("The 'write_file' tool is not available in the workspace.")
    assert _looks_like_refusal("I apologize for the confusion.")
    assert _looks_like_refusal("I cannot do this task.")


def test_does_not_flag_a_real_plan():
    plan = "1. In src/skytrap/tools/git.py, add a new function.\n2. Add a test."
    assert not _looks_like_refusal(plan)
