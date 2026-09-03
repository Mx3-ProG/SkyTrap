import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError


class Decision(BaseModel):
    """The model's structured response: either call a tool or give a final answer."""

    type: Literal["tool_call", "final"]
    tool: str | None = None
    arguments: dict = Field(default_factory=dict)
    message: str | None = None


def _repair_final_message(text: str) -> str | None:
    """Best-effort recovery for a `{"type": "final", "message": "..."}` response whose
    message contains raw, unescaped double quotes (e.g. the model quoting a code
    snippet like {"path": ...} inline) — this breaks JSON string boundaries in a way
    strict/non-strict json.loads can't recover from, since the quotes are genuinely
    ambiguous to a real parser. Only handles the "final" shape; a malformed tool_call
    is deliberately left to fail rather than risk executing a guessed-at repair.
    """
    if not re.search(r'"type"\s*:\s*"final"', text):
        return None

    message_key = re.search(r'"message"\s*:\s*"', text)
    if not message_key:
        return None

    quote_start = message_key.end()
    quote_end = text.rfind('"')
    if quote_end <= quote_start:
        return None

    raw_message = text[quote_start:quote_end]
    return raw_message.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')


def _parse_decision(raw: str) -> Decision:
    """Shared by `skytrap.core.agent.run_agent_turn` (interactive/build) and
    `skytrap.autonomy.loop.AgentLoop` (autonomous `agent run`) — the same JSON
    tool_call/final protocol is parsed identically in both, one implementation,
    living here rather than in `core.agent` specifically to avoid a circular
    import (`core.agent` needs `skytrap.autonomy.executor.ToolExecutor` to route
    every write through the same policy engine as the autonomous runtime)."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[len("json") :]
        text = text.strip()

    for strict in (True, False):
        # strict=False tolerates literal control characters (raw newlines, tabs) inside
        # JSON strings — models routinely emit multi-line "message" values that way
        # instead of escaping them as \n, which strict JSON rejects.
        try:
            data = json.loads(text, strict=strict)
            return Decision.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            continue

    repaired = _repair_final_message(text)
    if repaired is not None:
        return Decision(type="final", message=repaired)

    # Model didn't follow the protocol at all — treat its raw text as the final answer
    # rather than failing the turn outright.
    return Decision(type="final", message=raw)
