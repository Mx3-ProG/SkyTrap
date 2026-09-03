from skytrap.autonomy.approval import ApprovalEngine
from skytrap.autonomy.executor import ToolExecutor
from skytrap.autonomy.memory import WorkingMemory
from skytrap.autonomy.risk import RiskEngine
from skytrap.autonomy.state import TaskState
from skytrap.core.agent import run_agent_turn
from skytrap.core.context import WorkspaceContext
from skytrap.models.base import ModelProvider

SUMMARIZER_ROLE_PROMPT = """You are acting as SkyTrap's Summarizer role. You are given a \
completed piece of work — a task and its outcome (a diff+summary, or a raw chat \
transcript). Write a SHORT note (3-6 sentences) capturing: what was attempted, what \
actually changed (name real files/functions), what's still open or unresolved, and \
anything a future session needs to know to pick this back up without re-reading \
everything.

Write for a future instance of yourself, not for a human — be terse and specific, \
skip pleasantries and restating the obvious. If nothing meaningful happened (e.g. just \
a greeting or a question that was answered with no changes), say so in one short \
sentence rather than padding it out.

Respond with type "final" containing only the note text — no preamble, no markdown \
headers."""


def run_summarizer(
    model: ModelProvider, workspace: WorkspaceContext, task: str, outcome_text: str
) -> str:
    """Summarizes a completed task/session into a short work-log note. No tools at
    all — it summarizes text it's already been given, it doesn't need to re-explore
    the workspace, which keeps it fast and cheap even on a small local model.
    """
    history: list[dict] = []
    prompt_input = f"Task: {task}\n\nOutcome:\n{outcome_text}"
    # Item 1 — UNIFY EXECUTION: even a tool-free role goes through the same
    # ToolExecutor (here with an empty toolset — there's nothing to call, but the
    # decision loop is identical) rather than a separate code path.
    executor = ToolExecutor([], RiskEngine(), ApprovalEngine())
    agent_task = TaskState(workspace_path=workspace.path, goal=task)
    agent_memory = WorkingMemory(objective=task)
    return run_agent_turn(
        model,
        executor,
        agent_task,
        agent_memory,
        workspace,
        history,
        prompt_input,
        role_prompt=SUMMARIZER_ROLE_PROMPT,
    )
