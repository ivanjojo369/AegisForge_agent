from .security_baseline import SecurityBaselinePlugin
from .openenv_baseline import OpenEnvBaselinePlugin
from .tau2_baseline import Tau2BaselinePlugin

__all__ = [
    "SecurityBaselinePlugin",
    "OpenEnvBaselinePlugin",
    "Tau2BaselinePlugin",
]
"""
This package contains example plugins that can be used as baselines or templates for building new plugins. They demonstrate how to implement the plugin interface and integrate with the AegisForge agent framework. These examples can be extended or modified to create custom plugins for specific tasks or domains.
"""
