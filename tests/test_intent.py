from skytrap.core.intent import detect_execution_intent, looks_like_consultant_refusal


def test_standalone_go_triggers_execution():
    assert detect_execution_intent("Go.") is True
    assert detect_execution_intent("go") is True
    assert detect_execution_intent("GO!") is True


def test_vas_y_triggers_execution():
    assert detect_execution_intent("Vas-y.") is True


def test_go_as_language_mention_does_not_trigger_standalone_match():
    # "go" only fires as a standalone exact match — these sentences aren't exactly
    # "go", so they must rely on their own trigger words (or none) rather than the
    # language name colliding with the execution trigger.
    assert detect_execution_intent("En Go, comment on gère les erreurs ?") is False
    assert detect_execution_intent("Golang est un bon choix ?") is False


def test_create_in_go_triggers_via_creation_verb_not_language_name():
    # From the spec: "Crée-moi cette API en Go." must trigger execution mode (via
    # "crée"), independently of "en Go" naming the language.
    assert detect_execution_intent("Crée-moi cette API en Go.") is True


def test_phrase_triggers():
    assert detect_execution_intent("Implémente le plan.") is True
    assert detect_execution_intent("Construis-le maintenant.") is True
    assert detect_execution_intent("Fais-le.") is True
    assert detect_execution_intent("build it") is True


def test_ordinary_questions_do_not_trigger_execution():
    assert detect_execution_intent("Comment fonctionne l'authentification ?") is False
    assert detect_execution_intent("Explique-moi ce fichier.") is False
    assert detect_execution_intent("Quelle est la différence entre A et B ?") is False


def test_looks_like_consultant_refusal_matches_reported_phrases():
    assert looks_like_consultant_refusal(
        "Je ne peux pas créer un projet entier, mais je peux vous montrer comment procéder."
    )
    assert looks_like_consultant_refusal("I cannot create an entire project, but I can show you how.")


def test_looks_like_consultant_refusal_does_not_match_a_real_completion():
    assert not looks_like_consultant_refusal("J'ai créé le fichier main.go et le build a réussi.")
    assert not looks_like_consultant_refusal("No changes needed — this can be answered directly.")
