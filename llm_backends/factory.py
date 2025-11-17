"""
LLM Backend Factory for AWS TagSense.

This module provides a factory for creating LLM backends with automatic fallback support.
If the primary backend fails, it automatically tries the fallback backend.
"""

import os
import logging
from typing import Optional, List
from functools import lru_cache
import hashlib
import json
import time

from llm_backends.base import (
    BaseLLMBackend,
    LLMProvider,
    LLMMessage,
    LLMResponse,
    LLMError,
    RateLimitError,
    AuthenticationError
)
from llm_backends.openai_backend import OpenAIBackend
from llm_backends.anthropic_backend import AnthropicBackend


logger = logging.getLogger(__name__)


class ResponseCache:
    """Simple in-memory cache for LLM responses.

    This cache reduces costs and latency by avoiding duplicate API calls
    for identical prompts.
    """

    def __init__(self, ttl: int = 3600):
        """Initialize response cache.

        Args:
            ttl: Time-to-live for cache entries in seconds (default: 1 hour)
        """
        self.cache = {}
        self.ttl = ttl

    def _make_key(self, messages: List[LLMMessage], model: str, temperature: float) -> str:
        """Create a cache key from messages and parameters.

        Args:
            messages: List of conversation messages
            model: Model name
            temperature: Temperature setting

        Returns:
            Hash string to use as cache key
        """
        # Create a string representation of the request
        cache_data = {
            "messages": [{"role": msg.role, "content": msg.content} for msg in messages],
            "model": model,
            "temperature": temperature
        }
        cache_str = json.dumps(cache_data, sort_keys=True)

        # Hash it
        return hashlib.md5(cache_str.encode()).hexdigest()

    def get(self, messages: List[LLMMessage], model: str, temperature: float) -> Optional[str]:
        """Get a cached response if available and not expired.

        Args:
            messages: List of conversation messages
            model: Model name
            temperature: Temperature setting

        Returns:
            Cached response content or None if not found/expired
        """
        key = self._make_key(messages, model, temperature)

        if key in self.cache:
            cached_response, timestamp = self.cache[key]

            # Check if expired
            if time.time() - timestamp < self.ttl:
                logger.debug(f"Cache hit for key: {key[:8]}...")
                return cached_response

            # Expired - remove it
            del self.cache[key]

        return None

    def set(self, messages: List[LLMMessage], model: str, temperature: float, response: str) -> None:
        """Store a response in the cache.

        Args:
            messages: List of conversation messages
            model: Model name
            temperature: Temperature setting
            response: Response content to cache
        """
        key = self._make_key(messages, model, temperature)
        self.cache[key] = (response, time.time())
        logger.debug(f"Cached response for key: {key[:8]}...")

    def clear(self) -> None:
        """Clear all cached responses."""
        self.cache.clear()
        logger.info("Response cache cleared")


class LLMBackendFactory:
    """Factory for creating and managing LLM backends with fallback support.

    This factory handles:
    - Backend creation based on configuration
    - Automatic fallback to secondary backend if primary fails
    - Response caching to reduce API costs
    - Logging and error handling

    Example:
        ```python
        factory = LLMBackendFactory(
            primary_backend="openai",
            fallback_backend="anthropic",
            enable_cache=True
        )

        # Generate with automatic fallback
        response = factory.generate_with_fallback(
            prompt="Explain AWS tagging",
            system_prompt="You are a cloud expert"
        )
        ```
    """

    def __init__(
        self,
        primary_backend: str = "openai",
        fallback_backend: Optional[str] = "anthropic",
        enable_cache: bool = True,
        cache_ttl: int = 3600,
        **backend_kwargs
    ):
        """Initialize the LLM backend factory.

        Args:
            primary_backend: Primary backend to use ("openai" or "anthropic")
            fallback_backend: Fallback backend if primary fails (None to disable)
            enable_cache: Whether to enable response caching
            cache_ttl: Cache time-to-live in seconds
            **backend_kwargs: Additional arguments passed to backend constructors
        """
        self.primary_backend_name = primary_backend
        self.fallback_backend_name = fallback_backend
        self.enable_cache = enable_cache
        self.backend_kwargs = backend_kwargs

        # Initialize cache
        self.cache = ResponseCache(ttl=cache_ttl) if enable_cache else None

        # Create backends
        self.primary_backend = self._create_backend(primary_backend)
        self.fallback_backend = None

        if fallback_backend:
            try:
                self.fallback_backend = self._create_backend(fallback_backend)
            except Exception as e:
                logger.warning(f"Could not initialize fallback backend '{fallback_backend}': {e}")

        logger.info(
            f"LLM Backend Factory initialized: "
            f"primary={primary_backend}, fallback={fallback_backend}, cache={enable_cache}"
        )

    def _create_backend(self, backend_name: str) -> BaseLLMBackend:
        """Create a specific backend instance.

        Args:
            backend_name: Name of the backend ("openai" or "anthropic")

        Returns:
            Initialized backend instance

        Raises:
            ValueError: If backend name is not recognized
        """
        backend_name = backend_name.lower()

        if backend_name == "openai":
            return OpenAIBackend(**self.backend_kwargs)
        elif backend_name == "anthropic":
            return AnthropicBackend(**self.backend_kwargs)
        else:
            raise ValueError(
                f"Unknown backend: {backend_name}. "
                f"Supported backends: openai, anthropic"
            )

    def generate(
        self,
        messages: List[LLMMessage],
        use_fallback: bool = True,
        **kwargs
    ) -> LLMResponse:
        """Generate a response with optional fallback support.

        Args:
            messages: List of conversation messages
            use_fallback: Whether to try fallback backend if primary fails
            **kwargs: Additional parameters passed to backend

        Returns:
            LLMResponse from primary or fallback backend

        Raises:
            LLMError: If both primary and fallback backends fail
        """
        # Check cache first
        if self.cache:
            cached_content = self.cache.get(
                messages,
                self.primary_backend.model,
                self.primary_backend.temperature
            )
            if cached_content:
                return LLMResponse(
                    content=cached_content,
                    model=self.primary_backend.model,
                    provider=self.primary_backend.get_provider(),
                    cached=True
                )

        # Try primary backend
        try:
            logger.info(f"Generating response with primary backend: {self.primary_backend}")
            response = self.primary_backend.generate(messages, **kwargs)

            # Cache successful response
            if self.cache:
                self.cache.set(
                    messages,
                    self.primary_backend.model,
                    self.primary_backend.temperature,
                    response.content
                )

            return response

        except (RateLimitError, AuthenticationError) as e:
            # Don't retry on auth errors or rate limits - these won't work on fallback either
            logger.error(f"Primary backend failed with non-retryable error: {e}")
            raise

        except LLMError as e:
            logger.warning(f"Primary backend failed: {e}")

            # Try fallback if enabled and available
            if use_fallback and self.fallback_backend:
                logger.info(f"Trying fallback backend: {self.fallback_backend}")
                try:
                    response = self.fallback_backend.generate(messages, **kwargs)

                    # Cache successful fallback response
                    if self.cache:
                        self.cache.set(
                            messages,
                            self.fallback_backend.model,
                            self.fallback_backend.temperature,
                            response.content
                        )

                    return response

                except Exception as fallback_error:
                    logger.error(f"Fallback backend also failed: {fallback_error}")
                    raise LLMError(
                        f"Both primary ({self.primary_backend_name}) and "
                        f"fallback ({self.fallback_backend_name}) backends failed. "
                        f"Primary error: {e}. Fallback error: {fallback_error}"
                    )

            # No fallback available
            raise

    def generate_simple(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        context: Optional[str] = None,
        use_fallback: bool = True,
        **kwargs
    ) -> str:
        """Simplified generation interface with fallback support.

        Args:
            prompt: The user prompt
            system_prompt: Optional system-level instructions
            context: Optional additional context
            use_fallback: Whether to use fallback backend if primary fails
            **kwargs: Additional parameters

        Returns:
            Generated text response

        Raises:
            LLMError: If generation fails
        """
        messages = []

        if system_prompt:
            messages.append(LLMMessage(role="system", content=system_prompt))

        user_content = prompt
        if context:
            user_content = f"Context:\n{context}\n\nQuestion:\n{prompt}"

        messages.append(LLMMessage(role="user", content=user_content))

        response = self.generate(messages, use_fallback=use_fallback, **kwargs)
        return response.content

    def get_active_backend(self) -> BaseLLMBackend:
        """Get the currently active primary backend.

        Returns:
            The primary backend instance
        """
        return self.primary_backend

    def clear_cache(self) -> None:
        """Clear the response cache."""
        if self.cache:
            self.cache.clear()


# Global factory instance (can be configured via config module)
_global_factory: Optional[LLMBackendFactory] = None


def get_llm_factory(
    primary_backend: Optional[str] = None,
    fallback_backend: Optional[str] = None,
    enable_cache: Optional[bool] = None,
    **kwargs
) -> LLMBackendFactory:
    """Get or create the global LLM backend factory.

    This function provides a singleton factory instance. On first call,
    it creates a factory with the provided configuration (or defaults from
    environment variables). Subsequent calls return the existing instance
    unless new configuration is explicitly provided.

    Args:
        primary_backend: Primary backend name (default: from LLM_PRIMARY_BACKEND env)
        fallback_backend: Fallback backend name (default: from LLM_FALLBACK_BACKEND env)
        enable_cache: Whether to enable caching (default: from ENABLE_CACHE env)
        **kwargs: Additional backend parameters

    Returns:
        Global LLMBackendFactory instance
    """
    global _global_factory

    # Use defaults from environment if not provided
    if primary_backend is None:
        primary_backend = os.getenv("LLM_PRIMARY_BACKEND", "openai")

    if fallback_backend is None:
        fallback_backend = os.getenv("LLM_FALLBACK_BACKEND", "anthropic")
        if fallback_backend.lower() == "none":
            fallback_backend = None

    if enable_cache is None:
        enable_cache = os.getenv("ENABLE_CACHE", "true").lower() == "true"

    # Create factory if it doesn't exist or if new config provided
    if _global_factory is None:
        _global_factory = LLMBackendFactory(
            primary_backend=primary_backend,
            fallback_backend=fallback_backend,
            enable_cache=enable_cache,
            **kwargs
        )

    return _global_factory
