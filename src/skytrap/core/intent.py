from typing import Literal

Intent = Literal["chat", "execute"]

# Exact match only (after stripping trailing punctuation and lowercasing) — this is
# what keeps "Go." from colliding with "en Go"/"Golang"/"avec Go", none of which are
# ever exactly the standalone string "go".
_STANDALONE_TRIGGERS = {"go", "vas-y", "vas y", "lance", "commence", "start"}

# Substring match — multi-word phrases or unambiguous verbs. Deliberately excludes
# bare "go" (handled above) so language mentions like "en Go"/"avec Go" never match
# here either. This is a curated heuristic keyword list, not real NLP/semantic
# understanding — broader than exact matching (per the "don't rely only on exact
# match" requirement) without overclaiming what it actually is.
_PHRASE_TRIGGERS = (
    "implémente", "implemente", "implement",
    "code-le", "code le", "code ça", "code ca", "code it",
    "construis", "build it",
    "fais-le", "fait le", "fais le",
    "exécute", "execute",
    "agis", "act now", "start coding",
    "crée", "créer", "create ",
    "programme", "programmer",
    "ajoute", "développe", "developpe", "écris", "ecris", "write the",
)

# Consultant-style hedging that should never be accepted as a completed answer to
# an execution request — verbatim/near-verbatim phrases from the reported bug,
# plus common generic hedges.
_CONSULTANT_REFUSAL_PATTERNS = (
    "i cannot create an entire project",
    "i can show you how",
    "i can provide examples",
    "je ne peux pas créer un projet entier",
    "je peux vous montrer comment",
    "je peux fournir",
    "je peux fournir des exemples",
    "voici comment vous pourriez",
    "souhaitez-vous que je",
    "voulez-vous que j'implémente",
    "i'm unable to build",
    "i am unable to build",
    "as an ai",
    "i recommend you",
    "you would need to",
    "you could start by",
    # Real observed model output (qwen2.5-coder:7b) offering to help instead of
    # helping — asking permission/confirmation to start is the same failure mode
    # as an explicit refusal: the user already asked, no further confirmation is
    # needed before acting.
    "let me know if",
    "if you'd like",
    "if you would like",
    "i can help you",
    "sounds good",
    "would you like me to",
    "do you want me to",
    "should i proceed",
    "should i go ahead",
    "shall i",
    "want me to",
    "let's get started",
    "to get started, you",
)


def detect_execution_intent(text: str) -> bool:
    """True when the message reads as an instruction to actually do the work now,
    as opposed to a question, an explanation request, or general chat. Used to
    decide whether run_agent_turn should require evidence (a real tool call) before
    accepting a "final" answer — see core.agent.run_agent_turn's
    require_execution_evidence parameter."""
    normalized = text.strip().lower().rstrip(".!?")
    if normalized in _STANDALONE_TRIGGERS:
        return True
    return any(phrase in normalized for phrase in _PHRASE_TRIGGERS)


def looks_like_consultant_refusal(text: str) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in _CONSULTANT_REFUSAL_PATTERNS)
