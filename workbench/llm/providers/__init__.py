"""LLM provider implementations."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from workbench.llm.providers.base import Provider

if TYPE_CHECKING:
    from workbench.config import LLMProviderConfig

logger = logging.getLogger(__name__)

__all__ = ["Provider", "create_provider"]


def create_provider(cfg: LLMProviderConfig) -> Provider | None:
    """
    Factory: build a Provider from a LLMProviderConfig.

    Returns None if the provider can't be created (missing API key, etc).
    """
    provider_type = getattr(cfg, "type", "openai")

    if provider_type == "claude-code":
        from workbench.llm.providers.claude_code import ClaudeCodeProvider
        return ClaudeCodeProvider(
            max_context=cfg.max_context_tokens,
            max_output=cfg.max_output_tokens,
            timeout=float(cfg.timeout_seconds),
        )

    # Default: OpenAI-compatible
    from workbench.llm.providers.openai_compat import OpenAICompatProvider

    api_key = ""
    if cfg.api_key_env:
        api_key = os.environ.get(cfg.api_key_env, "")
    if not api_key and cfg.api_key_env:
        logger.warning("No API key in env var %s for provider %s", cfg.api_key_env, cfg.name)
        return None

    return OpenAICompatProvider(
        url=cfg.api_base or "https://api.openai.com/v1",
        model=cfg.model,
        api_key=api_key,
        timeout=float(cfg.timeout_seconds),
        max_context=cfg.max_context_tokens,
        max_output=cfg.max_output_tokens,
    )
