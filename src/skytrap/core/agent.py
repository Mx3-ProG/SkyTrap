import json

from pydantic import ValidationError

from skytrap.core.context import WorkspaceContext
from skytrap.core.protocol import Decision
from skytrap.models.base import ModelProvider
from skytrap.tools.base import Tool

MAX_STEPS = 5

SYSTEM_PROMPT_TEMPLATE = """You are SkyTrap, a local coding assistant running in a terminal.

Workspace root: {workspace_path}

Available tools:
{tools_description}

You must always respond with a single JSON object and nothing else — no markdown \
code fences, no text outside the JSON.

To call a tool:
{{"type": "tool_call", "tool": "<tool_name>", "arguments": {{...}}}}

To answer the user directly (no tool needed):
{{"type": "final", "message": "<your response>"}}

Only call a tool when you genuinely need information from the workspace to answer. \
For greetings or general questions, respond immediately with type "final".
"""


def _build_system_prompt(workspace: WorkspaceContext, tools: list[Tool]) -> str:
    tools_description = "\n".join(f"- {tool.name}: {tool.description}" for tool in tools)
    return SYSTEM_PROMPT_TEMPLATE.format(
        workspace_path=workspace.path,
        tools_description=tools_description or "(none)",
    )


def _parse_decision(raw: str) -> Decision:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[len("json") :]
        text = text.strip()

    try:
        data = json.loads(text)
        return Decision.model_validate(data)
    except (json.JSONDecodeError, ValidationError):
        # Model didn't follow the protocol — treat its raw text as the final answer
        # rather than failing the turn outright.
        return Decision(type="final", message=raw)


def run_agent_turn(
    model: ModelProvider,
    tools: list[Tool],
    workspace: WorkspaceContext,
    history: list[dict],
    user_input: str,
) -> str:
    """Runs one observe -> decide -> act -> observe loop until the model gives a
    final answer or MAX_STEPS is reached. `history` is mutated in place so the
    conversation carries over between turns.
    """
    tools_by_name = {tool.name: tool for tool in tools}
    messages = [{"role": "system", "content": _build_system_prompt(workspace, tools)}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_input})

    for _ in range(MAX_STEPS):
        raw = model.chat(messages)
        decision = _parse_decision(raw)
        messages.append({"role": "assistant", "content": raw})

        if decision.type == "final":
            final_message = decision.message or raw
            history.append({"role": "user", "content": user_input})
            history.append({"role": "assistant", "content": final_message})
            return final_message

        tool = tools_by_name.get(decision.tool or "")
        if tool is None:
            observation = f"ERROR: unknown tool '{decision.tool}'"
        else:
            result = tool.execute(workspace, decision.arguments)
            observation = result.output if result.success else f"ERROR: {result.output}"

        messages.append(
            {"role": "user", "content": f"Tool result for {decision.tool}:\n{observation}"}
        )

    timeout_message = "I couldn't complete this within the allowed number of steps."
    history.append({"role": "user", "content": user_input})
    history.append({"role": "assistant", "content": timeout_message})
    return timeout_message
