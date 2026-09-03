from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from skytrap.core.context import WorkspaceContext


class IntentRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IntentMessage(BaseModel):
    role: str = "user"
    content: str


class IntentContext(BaseModel):
    """Bounded evidence available to the intent interpreter.

    Items are ordered oldest to newest. The current input is deliberately kept
    separate so an older message can never silently override it.
    """

    recent_conversation: list[IntentMessage] = Field(default_factory=list)
    current_objective: str | None = None
    workspace_path: Path | None = None
    repository_name: str | None = None
    previously_mentioned_entities: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)


class NormalizedIntent(BaseModel):
    raw_input: str
    interpreted_goal: str
    explicit_requirements: list[str] = Field(default_factory=list)
    implicit_constraints: list[str] = Field(default_factory=list)
    referenced_entities: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    risk: IntentRisk = IntentRisk.LOW
    confidence: float = Field(ge=0.0, le=1.0)
    clarification_required: bool = False
    clarification_question: str | None = None
    actionable: bool = True
    consequence: float = Field(default=0.2, ge=0.0, le=1.0)
    reversibility: float = Field(default=0.8, ge=0.0, le=1.0)


_ACTION_WORDS = re.compile(
    r"\b(fais|faire|corrige|corige|répare|repare|fix|investigate|enquête|cherche|change|modifie|modifi|"
    r"ajoute|ajoutee|supprime|suprime|efface|retire|remove|delete|drop|migre|déploie|deploy|renomme|"
    r"remets|garde|implémente|implemente|implement|build|teste|test|lance|run|update|"
    r"améliore|ameliore|rends|make|mets|set|stop|crée|cree|create|implemante)\b",
    re.IGNORECASE,
)
_EMOTIONAL_WORDS = re.compile(
    r"\b(me rend fou|j'en ai marre|ça me saoule|ca me saoule|je déteste|je deteste|"
    r"quelle horreur|c'est nul|this sucks|drives me crazy|hate this|kill me)\b",
    re.IGNORECASE,
)
_PROBLEM_REPORT = re.compile(
    r"\b(bug|bugue|déconne|deconne|cassé|casse|broken|fails?|échoue|echoue|"
    r"marche (?:toujours )?pas|doesn['’]?t work|not working)\b",
    re.IGNORECASE,
)
_REFERENCE_WORDS = re.compile(
    r"\b(ça|ca|ceci|cela|celui(?:-là| là| d'avant)?|celle(?:-là| là| d'avant)?|"
    r"le truc|le bouton là|comme avant|pareil(?: sur mobile)?|it|that|this one|same thing)\b",
    re.IGNORECASE,
)
_DESTRUCTIVE = re.compile(
    r"\b(supprime|efface|delete|remove|drop|truncate|reset --hard|wipe|destroy|purge)\b",
    re.IGNORECASE,
)
_HIGH_CONSEQUENCE = re.compile(
    r"\b(prod(?:uction)?|base de données|database|migration|migre|secret|token|clé api|"
    r"api key|password|mot de passe|deploy|déploie|push|billing|paiement)\b",
    re.IGNORECASE,
)
_LOW_COST = re.compile(r"\b(css|style|couleur|spacing|animation|texte|copy|local)\b", re.IGNORECASE)
_FILE_OR_ENDPOINT = re.compile(
    r"(?<!\w)(/[A-Za-z0-9_.~/-]+|[A-Za-z0-9_.-]+\.(?:py|js|jsx|ts|tsx|css|html|json|"
    r"toml|yaml|yml|md|sql|go|rs|java|rb|php))(?!\w)"
)
_QUOTED = re.compile(r"[\"'“](.{2,80}?)[\"'”]")


class HumanIntentEngine:
    """Policy-oriented interpretation layer, not a prompt prettifier.

    The engine deliberately keeps safety-critical decisions deterministic. It
    gathers evidence, resolves references when there is one credible referent,
    scores uncertainty and cost independently, then decides whether acting is
    justified. This contract can later be enriched by a language model without
    giving that model authority over the clarification boundary.
    """

    def normalize(
        self,
        raw_input: str,
        *,
        context: IntentContext | None = None,
        workspace: WorkspaceContext | None = None,
    ) -> NormalizedIntent:
        context = context or IntentContext()
        if workspace is not None:
            context = context.model_copy(
                update={"workspace_path": workspace.path, "repository_name": workspace.name}
            )
        raw = " ".join(raw_input.strip().split())
        entities = self._entities(raw)
        requirements = self._requirements(raw)
        constraints = self._constraints(raw)
        assumptions: list[str] = []
        ambiguities: list[str] = []
        contradictions = self._contradictions(raw)

        interpreted = self._interpret_colloquial(raw)
        reference_match = _REFERENCE_WORDS.search(raw)
        if reference_match:
            candidates = self._reference_candidates(context)
            if len(candidates) == 1:
                referent = candidates[0]
                entities = list(dict.fromkeys([*entities, referent]))
                assumptions.append(
                    f'Interpreting "{reference_match.group(0)}" as referring to {referent}.'
                )
                interpreted = f"{interpreted} (referent: {referent})"
            elif len(candidates) > 1:
                ambiguities.append(
                    "The implicit reference could refer to " + " or ".join(candidates[:3])
                )
            else:
                ambiguities.append(
                    f'The reference "{reference_match.group(0)}" has no reliable antecedent.'
                )

        actionable = bool(_ACTION_WORDS.search(raw) or _PROBLEM_REPORT.search(raw))
        if _EMOTIONAL_WORDS.search(raw) and not actionable:
            ambiguities.append("The message expresses frustration but no operational change.")

        if self._looks_incomplete(raw):
            ambiguities.append("The requested technical target or outcome is incomplete.")

        if re.search(r"\b(finalement|actually|plutôt|plutot|non,? en fait)\b", raw, re.I):
            assumptions.append("The correction in the current message supersedes earlier preferences.")

        risk, consequence, reversibility = self._cost(raw)
        confidence = self._confidence(
            raw,
            actionable=actionable,
            ambiguities=ambiguities,
            contradictions=contradictions,
            resolved_reference=bool(reference_match and assumptions),
        )
        clarification_required = self._must_clarify(
            actionable=actionable,
            ambiguities=ambiguities,
            contradictions=contradictions,
            confidence=confidence,
            consequence=consequence,
            reversibility=reversibility,
        )

        if ambiguities and not clarification_required and actionable:
            assumptions.append(
                "Proceeding with the narrowest reversible interpretation and preserving unrelated behavior."
            )
            constraints.append("Keep changes local and reversible until the interpretation is confirmed by evidence.")

        question = self._question(ambiguities, contradictions, context) if clarification_required else None
        return NormalizedIntent(
            raw_input=raw_input,
            interpreted_goal=interpreted,
            explicit_requirements=requirements,
            implicit_constraints=list(dict.fromkeys(constraints)),
            referenced_entities=entities,
            assumptions=assumptions,
            ambiguities=ambiguities,
            contradictions=contradictions,
            risk=risk,
            confidence=confidence,
            clarification_required=clarification_required,
            clarification_question=question,
            actionable=actionable,
            consequence=consequence,
            reversibility=reversibility,
        )

    @staticmethod
    def context_from_memory(
        memory,
        workspace: WorkspaceContext,
        *,
        objective: str | None = None,
    ) -> IntentContext:
        return IntentContext(
            recent_conversation=[IntentMessage.model_validate(item) for item in memory.conversation[-12:]],
            current_objective=objective or memory.objective,
            workspace_path=workspace.path,
            repository_name=workspace.name,
            previously_mentioned_entities=memory.referenced_entities[-20:],
            decisions=memory.decisions[-20:],
        )

    @staticmethod
    def _entities(text: str) -> list[str]:
        values = [match.group(1) for match in _FILE_OR_ENDPOINT.finditer(text)]
        values.extend(match.group(1) for match in _QUOTED.finditer(text))
        for noun in ("login", "auth", "API", "endpoint", "mobile", "bouton", "button"):
            if re.search(rf"\b{re.escape(noun)}\b", text, re.I):
                values.append(noun.lower())
        return list(dict.fromkeys(values))

    @staticmethod
    def _requirements(text: str) -> list[str]:
        clauses = [item.strip(" .") for item in re.split(r"[,;]|\b(?:et puis|puis|and then)\b", text)]
        return [clause for clause in clauses if clause and _ACTION_WORDS.search(clause)]

    @staticmethod
    def _constraints(text: str) -> list[str]:
        constraints: list[str] = []
        for clause in re.split(
            r"[,;]|\b(?:et|and)\b(?=\s+(?:change|modifie|ajoute|supprime|remove|keep|garde))",
            text,
            flags=re.I,
        ):
            clause = clause.strip(" .")
            clause = re.sub(r"^(?:finalement|actually|plutôt|plutot)\s+", "", clause, flags=re.I)
            if re.search(r"\b(ne|sans|sauf|garde|don't|do not|keep|except)\b", clause, re.I):
                constraints.append(clause)
        if re.search(r"\b(garde le reste|keep the rest|sans redesign|without redesign)\b", text, re.I):
            constraints.append("Preserve unrelated behavior and files.")
        return constraints

    @staticmethod
    def _contradictions(text: str) -> list[str]:
        contradictions: list[str] = []
        protected_domains = {
            "API": r"(?:ne\s+(?:change|modifie)\s+(?:surtout\s+)?pas|don't change|do not change)\s+(?:l['’])?api",
            "database": r"(?:ne\s+(?:change|touche)\s+pas|don't change|do not touch)\s+(?:la\s+)?(?:db|database|base)",
            "authentication": r"(?:ne\s+(?:change|touche)\s+pas|don't change|do not touch)\s+(?:l['’])?(?:auth|login)",
        }
        mutation_signals = {
            "API": r"\b(modifie|change|remove|supprime|ajoute)\b.{0,50}(?:endpoint|route|/\w+)",
            "database": r"\b(modifie|change|migre|drop|supprime)\b.{0,50}(?:db|database|base|table)",
            "authentication": r"\b(modifie|change|supprime|remove)\b.{0,50}(?:auth|login)",
        }
        for domain, protected in protected_domains.items():
            if re.search(protected, text, re.I) and re.search(mutation_signals[domain], text, re.I):
                contradictions.append(
                    f"The request both protects {domain} and asks for a change within that boundary."
                )
        return contradictions

    @staticmethod
    def _interpret_colloquial(text: str) -> str:
        if re.search(r"login.*(?:déconne|deconne|bug|broken).*(?:refresh|reload).*(?:tej|jette|déconnect)", text, re.I):
            return "Investigate and fix authentication state being lost after a browser refresh."
        if re.search(r"(?:refresh|reload).*(?:tej|jette|déconnect).*(?:login|auth)", text, re.I):
            return "Investigate and fix authentication state being lost after a browser refresh."
        replacements = {
            r"\bça me tej\b": "the session is lost",
            r"\bca me tej\b": "the session is lost",
            r"\bil déconne\b": "is malfunctioning",
            r"\bil deconne\b": "is malfunctioning",
        }
        result = text
        for pattern, replacement in replacements.items():
            result = re.sub(pattern, replacement, result, flags=re.I)
        return result

    @staticmethod
    def _reference_candidates(context: IntentContext) -> list[str]:
        candidates = list(reversed(context.previously_mentioned_entities))
        if not candidates:
            for message in reversed(context.recent_conversation):
                candidates.extend(HumanIntentEngine._entities(message.content))
                if candidates:
                    break
        return list(dict.fromkeys(candidates))[:3]

    @staticmethod
    def _looks_incomplete(text: str) -> bool:
        stripped = text.rstrip(" .!?")
        return bool(
            re.search(r"\b(le truc|the thing|tu sais|you know)$", stripped, re.I)
            or re.search(r"\b(supprime|remove|change|modifie|fix|corrige)\s+(ça|ca|it|that)$", stripped, re.I)
        )

    @staticmethod
    def _cost(text: str) -> tuple[IntentRisk, float, float]:
        destructive = bool(_DESTRUCTIVE.search(text))
        high = bool(_HIGH_CONSEQUENCE.search(text))
        low_cost = bool(_LOW_COST.search(text))
        if destructive and high:
            return IntentRisk.CRITICAL, 1.0, 0.05
        if destructive:
            return IntentRisk.HIGH, 0.8, 0.15
        if high:
            return IntentRisk.HIGH, 0.75, 0.3
        if low_cost:
            return IntentRisk.LOW, 0.2, 0.9
        if _ACTION_WORDS.search(text):
            return IntentRisk.MEDIUM, 0.45, 0.75
        return IntentRisk.LOW, 0.1, 1.0

    @staticmethod
    def _confidence(
        text: str,
        *,
        actionable: bool,
        ambiguities: list[str],
        contradictions: list[str],
        resolved_reference: bool,
    ) -> float:
        score = 0.88 if actionable else 0.48
        score -= min(0.45, 0.18 * len(ambiguities))
        score -= min(0.5, 0.35 * len(contradictions))
        if resolved_reference:
            score += 0.08
        if len(text.split()) < 3:
            score -= 0.12
        return round(max(0.0, min(1.0, score)), 2)

    @staticmethod
    def _must_clarify(
        *,
        actionable: bool,
        ambiguities: list[str],
        contradictions: list[str],
        confidence: float,
        consequence: float,
        reversibility: float,
    ) -> bool:
        if contradictions or not actionable:
            return True
        ambiguity = min(1.0, len(ambiguities) * 0.5)
        wrong_path_cost = ambiguity * (1.0 - confidence) * consequence * (1.0 + (1.0 - reversibility))
        return bool(ambiguities and (consequence >= 0.7 or reversibility <= 0.3 or wrong_path_cost >= 0.12))

    @staticmethod
    def _question(
        ambiguities: list[str], contradictions: list[str], context: IntentContext
    ) -> str:
        if contradictions:
            return "Which instruction should take priority before I change that boundary?"
        if ambiguities:
            return "Which path did you mean? " + ambiguities[0]
        if context.current_objective:
            return f"What concrete outcome should I apply to {context.current_objective}?"
        return "What concrete outcome would you like SkyTrap to implement?"
