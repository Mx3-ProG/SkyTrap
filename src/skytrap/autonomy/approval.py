from __future__ import annotations

from enum import StrEnum
from typing import Callable

from pydantic import BaseModel

from skytrap.autonomy.risk import RiskAssessment, RiskLevel


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    DENIED = "denied"
    PENDING = "pending"


class ApprovalRequest(BaseModel):
    task_id: str
    tool_name: str
    arguments: dict
    assessment: RiskAssessment


class ApprovalEngine:
    def __init__(
        self,
        callback: Callable[[ApprovalRequest], ApprovalDecision | bool | None] | None = None,
        auto_approve_through: RiskLevel = RiskLevel.MEDIUM,
    ):
        self.callback = callback
        self.auto_approve_through = auto_approve_through

    def decide(self, request: ApprovalRequest) -> ApprovalDecision:
        if request.assessment.level <= self.auto_approve_through and not request.assessment.requires_approval:
            return ApprovalDecision.APPROVED
        if self.callback is None:
            return ApprovalDecision.PENDING
        decision = self.callback(request)
        if isinstance(decision, ApprovalDecision):
            return decision
        if decision is True:
            return ApprovalDecision.APPROVED
        if decision is False:
            return ApprovalDecision.DENIED
        return ApprovalDecision.PENDING
