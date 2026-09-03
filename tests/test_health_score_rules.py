"""Item 12 — HEALTH SCORE RULES: the composite "Autonomous Coding Readiness"
score is not a naive average; a weak critical dimension (repository
understanding, editing precision, verification) caps the whole composite.
"""

from skytrap.core.doctor import CRITICAL_DIMENSIONS, HealthDimension, autonomous_coding_readiness


def _dimensions(**overrides: float) -> list[HealthDimension]:
    names = [
        "Repository understanding", "Context intelligence", "Human intent", "Planning",
        "Editing precision", "Model intelligence", "Verification", "Review",
        "Project memory", "Technology freshness", "Safety",
    ]
    return [HealthDimension(name, overrides.get(name, 9.0), "") for name in names]


def test_weak_verification_caps_the_composite_even_with_strong_everything_else():
    dimensions = _dimensions(Verification=6.0)
    score, explanation = autonomous_coding_readiness(dimensions)
    assert score <= 7.0
    assert "Verification" in explanation


def test_composite_is_not_a_naive_average():
    dimensions = _dimensions(Verification=6.0)
    naive_average = sum(d.score for d in dimensions) / len(dimensions)
    score, _ = autonomous_coding_readiness(dimensions)
    assert score < naive_average


def test_all_strong_dimensions_score_high():
    dimensions = _dimensions()
    score, _ = autonomous_coding_readiness(dimensions)
    assert score >= 8.5


def test_every_critical_dimension_is_actually_checked():
    dimensions = _dimensions()
    names = {d.name for d in dimensions}
    assert set(CRITICAL_DIMENSIONS) <= names


def test_empty_dimensions_does_not_crash():
    score, explanation = autonomous_coding_readiness([])
    assert isinstance(score, float)
    assert explanation
