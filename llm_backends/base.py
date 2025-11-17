"""
Base LLM Backend Interface for AWS TagSense.

This module defines the abstract base class that all LLM backends must implement,
providing a consistent interface for interacting with different LLM providers
(OpenAI, Anthropic/Claude, etc.).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Any
from enum import Enum


class LLMProvider(Enum):
    """Supported LLM providers."""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


@dataclass
class LLMMessage:
    """Represents a message in a conversation.

    Attributes:
        role: The role of the message sender (system, user, assistant)
        content: The content of the message
    """
    role: str  # "system", "user", or "assistant"
    content: str


@dataclass
class LLMResponse:
    """Standardized response from any LLM backend.

    Attributes:
        content: The generated text response
        model: The model used for generation
        provider: The LLM provider that generated the response
        usage: Token usage information (if available)
        cached: Whether this response was retrieved from cache
    """
    content: str
    model: str
    provider: LLMProvider
    usage: Optional[Dict[str, int]] = None  # e.g., {"prompt_tokens": 100, "completion_tokens": 50}
    cached: bool = False


class BaseLLMBackend(ABC):
    """Abstract base class for all LLM backends.

    All LLM provider integrations (OpenAI, Anthropic, etc.) must inherit from this
    class and implement the required methods. This ensures a consistent interface
    across all providers and enables easy swapping between providers.

    Attributes:
        model: The specific model to use (e.g., "gpt-4", "claude-3-5-sonnet-20241022")
        temperature: Controls randomness in generation (0.0 = deterministic, 1.0 = creative)
        max_tokens: Maximum number of tokens in the response
        api_key: API key for the provider (loaded from environment or config)
    """

    def __init__(
        self,
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        api_key: Optional[str] = None
    ):
        """Initialize the LLM backend.

        Args:
            model: The model identifier to use
            temperature: Temperature for generation (0.0-1.0)
            max_tokens: Maximum tokens in response
            api_key: API key for authentication (if None, loaded from environment)

        Raises:
            ValueError: If required configuration is missing
        """
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.api_key = api_key

        # Validate configuration
        self._validate_config()

    @abstractmethod
    def _validate_config(self) -> None:
        """Validate that all required configuration is present.

        Raises:
            ValueError: If required configuration (e.g., API key) is missing
        """
        pass

    @abstractmethod
    def generate(
        self,
        messages: List[LLMMessage],
        **kwargs
    ) -> LLMResponse:
        """Generate a response from the LLM.

        This is the core method that all backends must implement. It takes a list
        of messages (conversation history) and returns a standardized response.

        Args:
            messages: List of conversation messages
            **kwargs: Additional provider-specific parameters

        Returns:
            LLMResponse object containing the generated content and metadata

        Raises:
            LLMError: If the API call fails
            RateLimitError: If rate limit is exceeded
        """
        pass

    @abstractmethod
    def generate_simple(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        context: Optional[str] = None,
        **kwargs
    ) -> str:
        """Simplified generation interface for single prompts.

        This is a convenience method that wraps the full `generate()` method
        for simple use cases where you just want to send a single prompt.

        Args:
            prompt: The user prompt
            system_prompt: Optional system-level instructions
            context: Optional additional context to include
            **kwargs: Additional provider-specific parameters

        Returns:
            The generated text response

        Raises:
            LLMError: If the API call fails
        """
        pass

    @abstractmethod
    def get_provider(self) -> LLMProvider:
        """Get the provider type for this backend.

        Returns:
            LLMProvider enum value
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this backend is available and properly configured.

        Returns:
            True if the backend can be used, False otherwise
        """
        pass

    def __str__(self) -> str:
        """String representation of the backend."""
        return f"{self.get_provider().value}:{self.model}"

    def __repr__(self) -> str:
        """Developer-friendly representation."""
        return (
            f"{self.__class__.__name__}("
            f"model='{self.model}', "
            f"temperature={self.temperature}, "
            f"max_tokens={self.max_tokens})"
        )


class LLMError(Exception):
    """Base exception for LLM-related errors."""
    pass


class RateLimitError(LLMError):
    """Raised when the LLM API rate limit is exceeded."""
    pass


class AuthenticationError(LLMError):
    """Raised when API authentication fails."""
    pass


class ModelNotFoundError(LLMError):
    """Raised when the specified model is not available."""
    pass


class ContextLengthExceededError(LLMError):
    """Raised when the input exceeds the model's context length."""
    pass
