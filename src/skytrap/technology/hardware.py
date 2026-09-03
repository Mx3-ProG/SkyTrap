from __future__ import annotations

import os
import platform
import shutil
import subprocess
from enum import StrEnum

from pydantic import BaseModel


class HardwareFit(StrEnum):
    RECOMMENDED = "recommended"
    OPTIONAL = "optional"
    TOO_HEAVY = "too_heavy"
    INCOMPATIBLE = "incompatible"
    BENCHMARK_REQUIRED = "benchmark_required"


class HardwareProfile(BaseModel):
    os: str
    architecture: str
    cpu: str
    ram_gb: float | None = None
    unified_memory: bool = False
    gpu: str | None = None
    disk_available_gb: float

    @classmethod
    def detect(cls, path: str = ".") -> "HardwareProfile":
        system = platform.system()
        ram = cls._ram_gb(system)
        gpu = cls._gpu(system)
        return cls(
            os=system,
            architecture=platform.machine(),
            cpu=platform.processor() or platform.machine(),
            ram_gb=ram,
            unified_memory=system == "Darwin" and platform.machine() == "arm64",
            gpu=gpu,
            disk_available_gb=round(shutil.disk_usage(path).free / 1024**3, 2),
        )

    def fit_model(self, required_memory_gb: float | None, required_arch: str | None = None) -> HardwareFit:
        if required_arch and required_arch != self.architecture:
            return HardwareFit.INCOMPATIBLE
        if required_memory_gb is None or self.ram_gb is None:
            return HardwareFit.BENCHMARK_REQUIRED
        if self.disk_available_gb < required_memory_gb:
            return HardwareFit.TOO_HEAVY
        if self.ram_gb >= required_memory_gb * 1.5:
            return HardwareFit.RECOMMENDED
        if self.ram_gb >= required_memory_gb:
            return HardwareFit.OPTIONAL
        return HardwareFit.TOO_HEAVY

    @staticmethod
    def _ram_gb(system: str) -> float | None:
        try:
            if system == "Darwin":
                try:
                    value = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=2, check=True).stdout
                    return round(int(value.strip()) / 1024**3, 2)
                except (OSError, ValueError, subprocess.SubprocessError):
                    pass
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            return round(pages * page_size / 1024**3, 2)
        except (OSError, ValueError, subprocess.SubprocessError):
            return None

    @staticmethod
    def _gpu(system: str) -> str | None:
        command = ["system_profiler", "SPDisplaysDataType"] if system == "Darwin" else ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"]
        if not shutil.which(command[0]):
            return None
        try:
            output = subprocess.run(command, capture_output=True, text=True, timeout=3).stdout.strip()
        except subprocess.SubprocessError:
            return None
        if not output:
            return None
        for line in output.splitlines():
            if "Chipset Model:" in line:
                return line.split(":", 1)[1].strip()[:160]
        return output.splitlines()[0][:160]
