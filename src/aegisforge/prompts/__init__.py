"""Prompt assets for AegisForge.

This package stores prompt text as versioned, track-aware assets rather than
keeping large prompt bodies hidden inside adapters or agent shells.
"""
from .prompt_manager import PromptManager
__all__ = ["PromptManager"]
