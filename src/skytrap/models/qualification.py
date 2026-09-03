from __future__ import annotations

import json
import resource
import platform
import re
import time
from enum import StrEnum

from pydantic import BaseModel, Field

from skytrap.models.base import ModelProvider


class QualificationStatus(StrEnum):
    QUALIFIED = "qualified"
    REJECTED = "rejected"
    ERROR = "error"


class QualificationProbe(BaseModel):
    name: str
    passed: bool
    latency_ms: float
    detail: str = ""
    responded: bool = True


class ModelQualificationResult(BaseModel):
    model: str
    success_rate: float | None = None
    structured_output_score: float | None = None
    coding_score: float | None = None
    repository_reasoning_score: float | None = None
    tool_selection_score: float | None = None
    latency_ms: float
    memory_usage_mb: float
    context_window: int
    hardware_fit: str = "benchmark_required"
    qualified: bool = False
    qualification_status: QualificationStatus
    probes: list[QualificationProbe] = Field(default_factory=list)

    @property
    def overall_score(self) -> float:
        scores = [self.coding_score, self.structured_output_score, self.repository_reasoning_score, self.tool_selection_score]
        available = [score for score in scores if score is not None]
        return round(sum(available) / len(available), 3) if available else 0.0

    @property
    def tool_score(self) -> float:
        return self.tool_selection_score or 0.0

    @property
    def reasoning_score(self) -> float:
        return self.repository_reasoning_score or 0.0


PROBES = (
    ("structured_json", 'Return only {"ok":true}.', lambda text: json.loads(text).get("ok") is True),
    ("tool_selection", 'Return only {"type":"tool_call","tool":"read_file","arguments":{"path":"app.py"}}.', lambda text: json.loads(text).get("tool") == "read_file"),
    ("repository_understanding", "A.py imports B.py. Which file is a direct dependency of A.py? Answer: B.py", lambda text: "b.py" in text.lower()),
    ("code_patch", "Fix `def add(a,b): return a-b`. Return the corrected return line only.", lambda text: "a+b" in text.replace(" ", "")),
    ("debugging", "x=None; x.upper() fails with which exception?", lambda text: "attribute" in text.lower()),
    ("long_context", "Remember marker RABBIT-731 and return it exactly.", lambda text: "RABBIT-731" in text),
    ("instruction_following", "Reply with exactly SKYTRAP_OK", lambda text: text.strip() == "SKYTRAP_OK"),
)


class ModelQualificationSuite:
    def run(self, provider: ModelProvider) -> ModelQualificationResult:
        probes: list[QualificationProbe] = []
        started_all = time.monotonic()
        for name, prompt, validate in PROBES:
            started = time.monotonic()
            try:
                answer = provider.chat([{"role": "user", "content": prompt}])
                responded = True
            except Exception as exc:  # noqa: BLE001
                passed, detail, responded = False, str(exc), False
            else:
                try:
                    passed = bool(validate(answer))
                    detail = "pass" if passed else "response did not satisfy the deterministic oracle"
                except Exception as exc:  # noqa: BLE001 - malformed output is a failed oracle, but still a real reply
                    passed, detail = False, f"model replied, but output validation failed: {exc}"
            probes.append(
                QualificationProbe(
                    name=name,
                    passed=passed,
                    latency_ms=round((time.monotonic() - started) * 1000, 2),
                    detail=detail,
                    responded=responded,
                )
            )
        response_count = sum(probe.responded for probe in probes)
        if response_count == 0:
            return ModelQualificationResult(
                model=provider.name,
                latency_ms=round((time.monotonic() - started_all) * 1000, 2),
                memory_usage_mb=self._memory_usage_mb(),
                context_window=provider.capabilities.context_window,
                qualification_status=QualificationStatus.ERROR,
                probes=probes,
            )
        by_name = {probe.name: probe.passed for probe in probes}
        coding = sum(by_name[name] for name in ("code_patch", "debugging", "instruction_following")) / 3
        structured = float(by_name["structured_json"])
        tools = float(by_name["tool_selection"])
        reasoning = sum(by_name[name] for name in ("repository_understanding", "debugging", "long_context")) / 3
        success_rate = sum(probe.passed for probe in probes) / len(probes)
        overall = (coding + structured + tools + reasoning) / 4
        qualified = overall >= 0.7 and response_count == len(probes)
        hardware_fit = "benchmark_required"
        try:
            from skytrap.technology.hardware import HardwareProfile

            size = re.search(r"(\d+(?:\.\d+)?)b(?:$|[-:])", provider.name.lower())
            required_gb = float(size.group(1)) * 0.7 if size else provider.profile.memory_gb
            hardware_fit = HardwareProfile.detect().fit_model(required_gb).value
        except Exception:  # noqa: BLE001 - qualification remains useful if hardware inspection is unavailable
            pass
        return ModelQualificationResult(
            model=provider.name,
            success_rate=round(success_rate, 3),
            structured_output_score=round(structured, 3),
            coding_score=round(coding, 3),
            tool_selection_score=round(tools, 3),
            repository_reasoning_score=round(reasoning, 3),
            latency_ms=round((time.monotonic() - started_all) * 1000, 2),
            memory_usage_mb=self._memory_usage_mb(),
            context_window=provider.capabilities.context_window,
            hardware_fit=hardware_fit,
            qualified=qualified,
            qualification_status=(QualificationStatus.QUALIFIED if qualified else QualificationStatus.REJECTED),
            probes=probes,
        )

    @staticmethod
    def _memory_usage_mb() -> float:
        return round(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            / (1024**2 if platform.system() == "Darwin" else 1024),
            2,
        )

    @staticmethod
    def should_switch(current: ModelQualificationResult, candidate: ModelQualificationResult, minimum_gain: float = 0.05) -> bool:
        return (
            candidate.qualification_status == QualificationStatus.QUALIFIED
            and candidate.overall_score >= current.overall_score + minimum_gain
        )
