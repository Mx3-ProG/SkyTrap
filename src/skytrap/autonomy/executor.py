from __future__ import annotations

from time import monotonic
from uuid import uuid4

from skytrap.autonomy.approval import ApprovalDecision, ApprovalEngine, ApprovalRequest
from skytrap.autonomy.memory import WorkingMemory
from skytrap.autonomy.risk import Capability, RiskEngine
from skytrap.autonomy.state import TaskState
from skytrap.core.context import WorkspaceContext
from skytrap.tools.base import Tool, ToolResult


class ToolExecutor:
    """Policy boundary between model-requested actions and concrete tools."""

    def __init__(
        self,
        tools: list[Tool],
        risk_engine: RiskEngine,
        approval_engine: ApprovalEngine,
        capabilities: set[Capability] | None = None,
    ):
        self.tools = {tool.name: tool for tool in tools}
        self.risk_engine = risk_engine
        self.approval_engine = approval_engine
        self.capabilities = capabilities or {
            Capability.FILESYSTEM_READ,
            Capability.FILESYSTEM_WRITE,
            Capability.SHELL_EXECUTE,
        }

    def execute(
        self,
        task: TaskState,
        memory: WorkingMemory,
        workspace: WorkspaceContext,
        tool_name: str,
        arguments: dict,
    ) -> ToolResult:
        call_id = uuid4().hex
        assessment = self.risk_engine.assess(tool_name, arguments)
        metadata = {
            "tool_call_id": call_id,
            "task_id": task.task_id,
            "run_id": task.run_id,
            "risk_level": assessment.level.name,
            "capability": assessment.capability.value,
        }

        tool = self.tools.get(tool_name)
        if tool is None:
            result = ToolResult(
                success=False,
                output=f"Unknown tool: {tool_name}",
                stderr=f"Unknown tool: {tool_name}",
                metadata=metadata,
            )
            self._record(memory, tool_name, arguments, result, call_id)
            return result
        if assessment.capability not in self.capabilities:
            result = ToolResult(
                success=False,
                status="denied",
                output=f"Missing capability: {assessment.capability.value}",
                stderr=f"Missing capability: {assessment.capability.value}",
                metadata=metadata,
            )
            self._record(memory, tool_name, arguments, result, call_id)
            return result

        request = ApprovalRequest(
            task_id=task.task_id,
            tool_name=tool_name,
            arguments=arguments,
            assessment=assessment,
        )
        decision = self.approval_engine.decide(request)
        if decision == ApprovalDecision.PENDING:
            task.pending_approval = request.model_dump(mode="json")
            result = ToolResult(
                success=False,
                status="needs_approval",
                output=f"Approval required for {tool_name}",
                metadata=metadata,
            )
            self._record(memory, tool_name, arguments, result, call_id)
            return result
        if decision == ApprovalDecision.DENIED:
            result = ToolResult(
                success=False,
                status="denied",
                output=f"Approval denied for {tool_name}",
                metadata=metadata,
            )
            self._record(memory, tool_name, arguments, result, call_id)
            return result

        started = monotonic()
        try:
            result = tool.execute(workspace, arguments)
        except Exception as exc:  # noqa: BLE001 - normalize tool failures at the boundary
            result = ToolResult(success=False, output=str(exc), stderr=str(exc))
        result.metadata = {**result.metadata, **metadata, "duration_ms": round((monotonic() - started) * 1000, 2)}
        if not result.stdout and result.success:
            result.stdout = result.output
        if not result.stderr and not result.success:
            result.stderr = result.output
        self._record(memory, tool_name, arguments, result, call_id)
        return result

    @staticmethod
    def _record(
        memory: WorkingMemory,
        tool_name: str,
        arguments: dict,
        result: ToolResult,
        call_id: str,
    ) -> None:
        memory.record(
            "tool_result",
            tool=tool_name,
            path=arguments.get("path"),
            command=arguments.get("command"),
            success=result.success,
            status=result.status,
            error=result.stderr if not result.success else None,
            tool_call_id=call_id,
        )
