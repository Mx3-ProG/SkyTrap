from dataclasses import dataclass
from typing import Callable

from skytrap.memory.sqlite import SqliteMemory
from skytrap.tools.base import Tool


@dataclass
class RegistryContext:
    """Shared dependencies a registered skill's factory can pull from, without each
    skill needing to know how to construct them itself."""

    memory: SqliteMemory
    confirm_write: Callable[[str], bool]


ToolFactory = Callable[[RegistryContext], Tool]

_FACTORIES: list[ToolFactory] = []


def register_tool(factory: ToolFactory) -> ToolFactory:
    """Decorator: a skill module calls this once, at import time, on a factory
    function that builds its Tool from a RegistryContext. Decorators only run when
    their module is actually imported — skills must be imported somewhere (see
    tools/skills/__init__.py) for this to have any effect; a skill file that's never
    imported silently contributes nothing, which is why that import point exists.
    """
    _FACTORIES.append(factory)
    return factory


def build_registered_tools(context: RegistryContext) -> list[Tool]:
    return [factory(context) for factory in _FACTORIES]


def clear_registry() -> None:
    """Test-only: registration is global module state, so tests that register a
    fake tool need a way to clean up after themselves rather than polluting every
    other test that imports this module."""
    _FACTORIES.clear()
