"""Item 7 — STRUCTURED EXECUTION EVIDENCE.

The final report must never be built by trusting the model's free-text claim
and patching an incorrect sentence with a regex — it must be built
deterministically from real ExecutionEvidence fields. A stage that never ran
must render as "not verified", never silently as passed.
"""

from skytrap.autonomy.evidence import ExecutionEvidence
from skytrap.autonomy.loop import _claim_validated_final_message
from skytrap.autonomy.memory import WorkingMemory


def _memory_with_write(path: str, *, is_new_file: bool) -> WorkingMemory:
    memory = WorkingMemory(objective="test")
    memory.record(
        "tool_result",
        tool="write_file",
        path=path,
        success=True,
        status="succeeded",
        is_new_file=is_new_file,
        is_delete=False,
    )
    return memory


def test_no_test_or_build_stage_run_renders_not_verified_not_passed():
    memory = _memory_with_write("app.py", is_new_file=False)
    evidence = ExecutionEvidence.from_memory(memory, success_criteria=["The login flow works end to end"])

    assert evidence.tests_passed is None
    assert evidence.build_passed is None
    report = evidence.render_structured_report()
    assert "Unit tests not verified." in report
    assert "Build not verified." in report
    assert "passed" not in report.lower().split("unit tests not verified.")[0]


def test_failed_test_never_reports_as_passed_and_marks_requirements_unverified():
    memory = _memory_with_write("app.py", is_new_file=False)
    memory.verification_results.append(
        {"results": [{"success": False, "metadata": {"stage": "test"}, "output": "1 failed"}]}
    )
    evidence = ExecutionEvidence.from_memory(memory, success_criteria=["Login works"])

    assert evidence.tests_passed is False
    assert evidence.tests_failed is True
    assert evidence.requirements_unverified == ["Login works"]
    assert evidence.requirements_satisfied == []
    report = evidence.render_structured_report()
    assert "Unit tests FAILED." in report


def test_all_real_stages_passing_marks_requirements_satisfied():
    memory = _memory_with_write("app.py", is_new_file=False)
    memory.verification_results.append(
        {"results": [{"success": True, "metadata": {"stage": "test"}, "output": "ok"}]}
    )
    evidence = ExecutionEvidence.from_memory(memory, success_criteria=["Login works"])

    assert evidence.tests_passed is True
    assert evidence.requirements_satisfied == ["Login works"]
    assert evidence.requirements_unverified == []


def test_browser_never_run_never_claims_end_to_end_verification():
    memory = _memory_with_write("app.py", is_new_file=False)
    evidence = ExecutionEvidence.from_memory(memory)
    assert evidence.browser_verified is None
    report = evidence.render_structured_report()
    assert "End-to-end browser behavior not verified." in report


def test_final_message_never_repeats_a_false_created_claim_as_fact():
    memory = _memory_with_write("index.html", is_new_file=False)
    evidence = ExecutionEvidence.from_memory(memory)
    message = _claim_validated_final_message(
        "I created index.html with the new homepage.", memory, evidence=evidence
    )

    # The deterministic report leads; the model's claim is present only as a
    # clearly-labeled, explicitly unverified quote — never asserted as fact.
    assert message.startswith("1 file(s) modified")
    assert 'Model\'s own summary (unverified' in message
    assert "already existed and was modified, not created" in message


def test_deterministic_report_never_says_implemented_successfully_without_evidence():
    memory = WorkingMemory(objective="test")  # nothing happened at all
    evidence = ExecutionEvidence.from_memory(memory)
    report = evidence.render_structured_report()
    assert "successfully" not in report.lower()
    assert "No files were changed." in report
