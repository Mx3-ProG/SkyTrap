import json
import subprocess

from rich.console import Console

from skytrap.autonomy import (
    ApprovalEngine,
    Capability,
    HumanIntentEngine,
    IntentContext,
    IntentMessage,
    IntentRisk,
    NormalizedIntent,
    Planner,
    RiskEngine,
    TaskState,
    TaskStatus,
    ToolExecutor,
    WorkingMemory,
)
from skytrap.autonomy.service import AutonomousTaskService
from skytrap.core.context import WorkspaceContext
from skytrap.models.base import ModelProvider
from skytrap.tools.filesystem import WriteFileTool
from skytrap.ui.terminal import TerminalCapabilities, print_agent_event


def context(*messages: str, entities: list[str] | None = None) -> IntentContext:
    return IntentContext(
        recent_conversation=[IntentMessage(content=message) for message in messages],
        current_objective="Improve authentication",
        previously_mentioned_entities=entities or [],
        decisions=["Keep the existing public API"],
    )


def test_colloquial_french_typo_and_mixed_language_are_operational():
    engine = HumanIntentEngine()

    slang = engine.normalize("le login il déconne encore quand je refresh ça me tej")
    typo = engine.normalize("corige le logn cassé puis run les tests")

    assert slang.actionable is True
    assert "authentication state" in slang.interpreted_goal
    assert slang.clarification_required is False
    assert typo.actionable is True
    assert typo.explicit_requirements


def test_previous_message_resolves_ca_and_celui_la_without_losing_raw_input():
    engine = HumanIntentEngine()
    prior = context("Le bouton login dans src/auth/Login.tsx", entities=["src/auth/Login.tsx"])

    for message in ("ça marche toujours pas", "corrige celui-là", "fais pareil sur mobile"):
        intent = engine.normalize(message, context=prior)
        assert intent.raw_input == message
        assert "src/auth/Login.tsx" in intent.referenced_entities
        assert intent.assumptions
        assert intent.clarification_required is False


def test_like_before_without_antecedent_forks_but_cheap_css_uses_narrow_assumption():
    engine = HumanIntentEngine()

    expensive = engine.normalize("remets comme avant puis déploie en production")
    cheap = engine.normalize("rends ça plus vivant en CSS")

    assert expensive.risk == IntentRisk.HIGH
    assert expensive.clarification_required is True
    assert expensive.clarification_question
    assert cheap.clarification_required is False
    assert any("narrowest reversible" in item for item in cheap.assumptions)


def test_current_change_of_mind_wins_over_old_context():
    intent = HumanIntentEngine().normalize(
        "Non, finalement garde le bouton et change seulement son texte",
        context=context("Supprime le bouton login"),
    )

    assert "garde le bouton" in intent.implicit_constraints
    assert any("supersedes" in item for item in intent.assumptions)
    assert "Supprime le bouton" not in intent.interpreted_goal


def test_contradiction_requires_clarification_before_planning():
    intent = HumanIntentEngine().normalize(
        "Ne change surtout pas l’API, mais modifie l’endpoint /login."
    )

    assert intent.contradictions
    assert intent.clarification_required is True
    assert intent.confidence < 0.7


def test_emotion_sarcasm_and_exaggeration_never_become_destructive_actions():
    engine = HumanIntentEngine()
    messages = (
        "ce fichier me rend fou",
        "Génial, ce module marche tellement bien...",
        "Je vais jeter ce laptop par la fenêtre",
    )

    for message in messages:
        intent = engine.normalize(message)
        assert intent.actionable is False
        assert intent.clarification_required is True
        assert "delete" not in intent.interpreted_goal.lower()
        assert "supprime" not in intent.interpreted_goal.lower()


def test_ambiguous_destructive_request_forks_while_clear_local_change_can_proceed():
    engine = HumanIntentEngine()

    destructive = engine.normalize(
        "supprime celui d'avant",
        context=context("deployment config", entities=["deploy.yml", "production.tf"]),
    )
    reversible = engine.normalize("change la couleur CSS du bouton login en vert")

    assert destructive.clarification_required is True
    assert len(destructive.ambiguities) == 1
    assert reversible.clarification_required is False
    assert reversible.reversibility > destructive.reversibility


class RecordingModel(ModelProvider):
    name = "recording"
    engine = "LOCAL"

    def __init__(self, response: dict | None = None):
        self.response = response or {}
        self.messages: list[list[dict]] = []

    def chat(self, messages: list[dict]) -> str:
        self.messages.append(messages)
        return json.dumps(self.response)


def test_planner_receives_structured_intent_contract(tmp_path):
    model = RecordingModel()
    intent = HumanIntentEngine().normalize("corrige le login et garde le reste")
    workspace = WorkspaceContext(path=tmp_path, name="repo", is_git=False)

    plan = Planner(model).create_plan(workspace, intent)

    prompt = model.messages[0][1]["content"]
    assert "Normalized human intent" in prompt
    assert '"raw_input": "corrige le login et garde le reste"' in prompt
    assert '"implicit_constraints"' in prompt
    assert plan.summary


def test_intent_risk_reaches_executor_and_approval(tmp_path):
    intent = HumanIntentEngine().normalize("modifie config.py puis déploie en production")
    task = TaskState(
        workspace_path=tmp_path,
        goal=intent.raw_input,
        normalized_intent=intent.model_dump(mode="json"),
    )
    memory = WorkingMemory(objective=task.goal)
    workspace = WorkspaceContext(path=tmp_path, name="repo", is_git=False)
    executor = ToolExecutor(
        [WriteFileTool(confirm=lambda _: True)],
        RiskEngine(),
        ApprovalEngine(),
        capabilities={Capability.FILESYSTEM_WRITE},
    )

    result = executor.execute(task, memory, workspace, "write_file", {"path": "config.py", "content": "x"})

    assert result.status == "needs_approval"
    assert result.metadata["risk_level"] == "HIGH"
    assert task.pending_approval["assessment"]["reasons"][-1] == "Human intent consequence level: high"


def _init_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    (path / "app.py").write_text("value = 1\n")
    subprocess.run(["git", "add", "app.py"], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", "init"],
        cwd=path,
        check=True,
    )


def test_ambiguous_destructive_task_persists_and_does_nothing_before_clarification(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    model = RecordingModel()
    service = AutonomousTaskService(model, store=None)
    service.store.root = tmp_path / "states"

    task = service.start(repo, "supprime celui d'avant en production")
    persisted, memory = service.store.load(task.task_id)

    assert task.status == TaskStatus.NEEDS_CLARIFICATION
    assert task.task_branch is None
    assert model.messages == []
    assert persisted.normalized_intent["raw_input"] == "supprime celui d'avant en production"
    assert memory.conversation[-1]["content"] == persisted.normalized_intent["raw_input"]
    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert not branch.startswith("skytrap/task-")


def test_path_forks_event_has_clean_ascii_fallback():
    output = Console(record=True, force_terminal=False, width=52, color_system=None)

    print_agent_event(
        {
            "kind": "path_forks",
            "paths": ["Remove legacy deployment", "Remove production config"],
            "question": "Which path did you mean?",
        },
        target_console=output,
        capabilities=TerminalCapabilities(unicode=False, color=False, interactive=False),
    )

    rendered = output.export_text()
    assert "THE PATH FORKS" in rendered
    assert "A  Remove legacy deployment" in rendered
    assert "B  Remove production config" in rendered
    assert "Which path did you mean?" in rendered


def test_working_assumptions_can_be_revised_with_an_audit_trail():
    memory = WorkingMemory(objective="Polish UI")
    memory.record_assumption("Treat alive as subtle animation")

    memory.revise_assumptions("User requested static rendering")

    assert memory.assumptions == []
    assert memory.assumption_history == [
        {
            "assumption": "Treat alive as subtle animation",
            "status": "revised",
            "reason": "User requested static rendering",
        }
    ]
    assert "User requested static rendering" in memory.corrections[0]
