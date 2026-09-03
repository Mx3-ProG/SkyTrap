from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class UpdateStep(StrEnum):
    DISCOVER = "discover"
    INSPECT_CHANGELOG = "inspect_changelog"
    COMPATIBILITY = "compatibility_analysis"
    SECURITY = "security_assessment"
    ISOLATED_INSTALL = "isolated_install"
    TEST = "test_suite"
    BENCHMARK = "benchmark"
    RECOMMEND = "user_recommendation"
    APPROVE = "explicit_approval"
    UPGRADE = "upgrade"
    ROLLBACK = "rollback_available"


class SafeUpdatePlan(BaseModel):
    technology: str
    steps: list[UpdateStep] = Field(default_factory=lambda: list(UpdateStep))
    requires_branch: bool = False
    requires_approval: bool = True
    automatic_upgrade: bool = False


class SafeUpdatePolicy:
    def plan(self, technology: str, *, major: bool = False) -> SafeUpdatePlan:
        return SafeUpdatePlan(technology=technology, requires_branch=major)
