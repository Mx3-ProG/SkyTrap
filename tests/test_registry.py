import pytest

from skytrap.tools.base import Tool, ToolResult
from skytrap.tools.registry import (
    RegistryContext,
    build_registered_tools,
    clear_registry,
    register_tool,
)


@pytest.fixture(autouse=True)
def isolated_registry():
    clear_registry()
    yield
    clear_registry()


class _FakeTool(Tool):
    name = "fake_tool"
    description = "a fake tool for testing the registry"

    def __init__(self, confirm):
        self._confirm = confirm

    def execute(self, workspace, arguments):
        return ToolResult(success=True, output="ok")


def test_empty_registry_builds_nothing():
    context = RegistryContext(memory=None, confirm_write=lambda preview: True)
    assert build_registered_tools(context) == []


def test_registered_factory_is_built_with_the_given_context():
    received_contexts = []

    @register_tool
    def _build_fake(context: RegistryContext) -> Tool:
        received_contexts.append(context)
        return _FakeTool(confirm=context.confirm_write)

    context = RegistryContext(memory="fake-memory", confirm_write=lambda preview: True)
    tools = build_registered_tools(context)

    assert len(tools) == 1
    assert tools[0].name == "fake_tool"
    assert received_contexts == [context]


def test_clear_registry_empties_it():
    @register_tool
    def _build_fake(context: RegistryContext) -> Tool:
        return _FakeTool(confirm=context.confirm_write)

    clear_registry()
    context = RegistryContext(memory=None, confirm_write=lambda preview: True)
    assert build_registered_tools(context) == []
