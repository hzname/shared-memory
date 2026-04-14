"""Shared Memory Awareness plugin for Hermes."""

import logging

from . import hooks

logger = logging.getLogger(__name__)


def register(ctx):
    """Register shared-memory awareness hooks."""
    ctx.register_hook("pre_llm_call", hooks.recall_context)
    ctx.register_hook("pre_tool_call", hooks.warn_on_tool)
    ctx.register_hook("post_tool_call", hooks.log_tool_result)

    logger.info("shared-memory-awareness: registered 3 hooks (pre_llm_call, pre_tool_call, post_tool_call)")
