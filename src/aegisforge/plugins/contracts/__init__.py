from .plugin_contract import AegisForgePlugin, PluginContext, PluginResult

__all__ = [
    "AegisForgePlugin",
    "PluginContext",
    "PluginResult",
]
"""
This package defines the core contract for plugins in the AegisForge agent framework. It includes the base plugin class (AegisForgePlugin) that all plugins should inherit from, as well as the PluginContext and PluginResult data structures that standardize how plugins receive input and return output. By adhering to this contract, plugins can be seamlessly integrated into the agent's workflow, allowing for consistent interaction patterns and easier maintenance.
"""
