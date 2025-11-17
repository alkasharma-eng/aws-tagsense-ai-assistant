"""
LLM Backends Module for AWS TagSense.

This module provides a pluggable architecture for integrating with multiple
LLM providers (OpenAI, Anthropic/Claude, etc.) with automatic fallback support
and response caching.
"""

from llm_backends.base import (
    BaseLLMBackend,
    LLMProvider,
    LLMMessage,
    LLMResponse,
    LLMError,
    RateLimitError,
    AuthenticationError,
    ModelNotFoundError,
    ContextLengthExceededError
)

from llm_backends.openai_backend import OpenAIBackend
from llm_backends.anthropic_backend import AnthropicBackend
from llm_backends.factory import LLMBackendFactory, get_llm_factory, ResponseCache

__all__ = [
    # Base classes and types
    "BaseLLMBackend",
    "LLMProvider",
    "LLMMessage",
    "LLMResponse",

    # Exceptions
    "LLMError",
    "RateLimitError",
    "AuthenticationError",
    "ModelNotFoundError",
    "ContextLengthExceededError",

    # Backend implementations
    "OpenAIBackend",
    "AnthropicBackend",

    # Factory and utilities
    "LLMBackendFactory",
    "get_llm_factory",
    "ResponseCache",
]
