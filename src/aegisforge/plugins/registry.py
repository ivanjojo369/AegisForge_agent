from __future__ import annotations

from typing import Iterable

from .base import BasePlugin, PluginContext


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, BasePlugin] = {}

    def register(self, plugin: BasePlugin) -> None:
        if plugin.name in self._plugins:
            raise ValueError(f"Plugin already registered: {plugin.name}")
        self._plugins[plugin.name] = plugin

    def unregister(self, name: str) -> None:
        self._plugins.pop(name, None)

    def get(self, name: str) -> BasePlugin | None:
        return self._plugins.get(name)

    def require(self, name: str) -> BasePlugin:
        plugin = self.get(name)
        if plugin is None:
            raise KeyError(f"Plugin not found: {name}")
        return plugin

    def list_names(self) -> list[str]:
        return sorted(self._plugins.keys())

    def list_plugins(self) -> list[BasePlugin]:
        return [self._plugins[name] for name in self.list_names()]

    def statuses(self) -> list[dict]:
        return [plugin.status() for plugin in self.list_plugins()]

    def setup_all(self) -> None:
        for plugin in self.list_plugins():
            if plugin.enabled:
                plugin.setup()

    def teardown_all(self) -> None:
        for plugin in self.list_plugins():
            if plugin.enabled:
                plugin.teardown()


def build_default_registry(
    plugins: Iterable[type[BasePlugin]] | None = None,
    *,
    context: PluginContext | None = None,
) -> PluginRegistry:
    registry = PluginRegistry()
    if not plugins:
        return registry

    plugin_context = context or PluginContext(agent_name="AegisForge")
    for plugin_cls in plugins:
        registry.register(plugin_cls(context=plugin_context))
    return registry
