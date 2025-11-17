"""
Anthropic Claude LLM Backend for AWS TagSense.

This module provides integration with Anthropic's Claude models (Claude 3.5 Sonnet,
Claude 3 Opus, etc.) following the BaseLLMBackend interface.
"""

import os
from typing import List, Optional
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)

try:
    from anthropic import Anthropic, AnthropicError, RateLimitError as AnthropicRateLimitError
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    Anthropic = None
    AnthropicError = Exception
    AnthropicRateLimitError = Exception

from llm_backends.base import (
    BaseLLMBackend,
    LLMMessage,
    LLMResponse,
    LLMProvider,
    LLMError,
    RateLimitError,
    AuthenticationError,
    ModelNotFoundError
)


class AnthropicBackend(BaseLLMBackend):
    """Anthropic Claude backend implementation.

    Supports Claude 3.5 Sonnet, Claude 3 Opus, and Claude 3 Sonnet with automatic
    retries, rate limit handling, and error recovery.

    Example:
        ```python
        backend = AnthropicBackend(
            model="claude-3-5-sonnet-20241022",
            temperature=0.3,
            api_key=os.getenv("ANTHROPIC_API_KEY")
        )

        response = backend.generate_simple(
            prompt="Explain AWS Lambda tagging best practices",
            system_prompt="You are a cloud compliance expert"
        )
        print(response)
        ```
    """

    SUPPORTED_MODELS = [
        "claude-3-5-sonnet-20241022",
        "claude-3-opus-20240229",
        "claude-3-sonnet-20240229",
        "claude-3-haiku-20240307"
    ]

    def __init__(
        self,
        model: str = "claude-3-5-sonnet-20241022",
        temperature: float = 0.3,
        max_tokens: int = 2048,
        api_key: Optional[str] = None
    ):
        """Initialize Anthropic backend.

        Args:
            model: Anthropic model to use (default: claude-3-5-sonnet-20241022)
            temperature: Temperature for generation (0.0-1.0)
            max_tokens: Maximum tokens in response
            api_key: Anthropic API key (if None, loads from ANTHROPIC_API_KEY env var)

        Raises:
            ValueError: If API key is missing or Anthropic library not installed
        """
        if not ANTHROPIC_AVAILABLE:
            raise ValueError(
                "Anthropic library is not installed. Install with: pip install anthropic"
            )

        # Load API key from environment if not provided
        if api_key is None:
            api_key = os.getenv("ANTHROPIC_API_KEY")

        super().__init__(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key
        )

        # Initialize Anthropic client
        self.client = Anthropic(api_key=self.api_key)

    def _validate_config(self) -> None:
        """Validate Anthropic configuration.

        Raises:
            ValueError: If API key is missing or model is not supported
        """
        if not self.api_key:
            raise ValueError(
                "Anthropic API key is required. Set ANTHROPIC_API_KEY environment variable "
                "or provide it as a parameter."
            )

        if not self.api_key.startswith("sk-ant-"):
            raise ValueError(
                "Invalid Anthropic API key format. API keys should start with 'sk-ant-'"
            )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(AnthropicRateLimitError),
        reraise=True
    )
    def generate(
        self,
        messages: List[LLMMessage],
        **kwargs
    ) -> LLMResponse:
        """Generate a response using Anthropic's API.

        This method includes automatic retry logic with exponential backoff
        for rate limit errors (429).

        Args:
            messages: List of conversation messages
            **kwargs: Additional Anthropic-specific parameters (e.g., top_p, top_k)

        Returns:
            LLMResponse containing the generated content and metadata

        Raises:
            RateLimitError: If rate limit exceeded after retries
            AuthenticationError: If API key is invalid
            ModelNotFoundError: If the specified model doesn't exist
            LLMError: For other API errors
        """
        try:
            # Separate system message from conversation messages
            # Anthropic requires system prompt as a separate parameter
            system_prompt = None
            conversation_messages = []

            for msg in messages:
                if msg.role == "system":
                    system_prompt = msg.content
                else:
                    conversation_messages.append({
                        "role": msg.role,
                        "content": msg.content
                    })

            # Build API call parameters
            api_params = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "messages": conversation_messages,
                **kwargs
            }

            # Add system prompt if present
            if system_prompt:
                api_params["system"] = system_prompt

            # Make API call
            response = self.client.messages.create(**api_params)

            # Extract response content
            content = response.content[0].text

            # Build usage information
            usage = None
            if response.usage:
                usage = {
                    "prompt_tokens": response.usage.input_tokens,
                    "completion_tokens": response.usage.output_tokens,
                    "total_tokens": response.usage.input_tokens + response.usage.output_tokens
                }

            return LLMResponse(
                content=content,
                model=response.model,
                provider=LLMProvider.ANTHROPIC,
                usage=usage,
                cached=False
            )

        except AnthropicRateLimitError as e:
            raise RateLimitError(
                f"Anthropic rate limit exceeded: {str(e)}. "
                "Please wait a moment and try again."
            )

        except AnthropicError as e:
            error_message = str(e)

            # Check for specific error types
            if "invalid_api_key" in error_message or "authentication" in error_message.lower():
                raise AuthenticationError(
                    f"Anthropic authentication failed: {error_message}. "
                    "Please check your API key."
                )

            if "model_not_found" in error_message or f"model '{self.model}'" in error_message:
                raise ModelNotFoundError(
                    f"Model '{self.model}' not found. "
                    f"Supported models: {', '.join(self.SUPPORTED_MODELS)}"
                )

            # Generic Anthropic error
            raise LLMError(f"Anthropic API error: {error_message}")

        except Exception as e:
            raise LLMError(f"Unexpected error during Anthropic API call: {str(e)}")

    def generate_simple(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        context: Optional[str] = None,
        **kwargs
    ) -> str:
        """Simplified interface for single-turn generation.

        Args:
            prompt: The user prompt
            system_prompt: Optional system-level instructions
            context: Optional additional context to prepend to the prompt
            **kwargs: Additional Anthropic parameters

        Returns:
            The generated text response

        Raises:
            LLMError: If generation fails
        """
        messages = []

        # Add system prompt if provided
        if system_prompt:
            messages.append(LLMMessage(role="system", content=system_prompt))

        # Build user message with optional context
        user_content = prompt
        if context:
            user_content = f"Context:\n{context}\n\nQuestion:\n{prompt}"

        messages.append(LLMMessage(role="user", content=user_content))

        # Generate response
        response = self.generate(messages, **kwargs)
        return response.content

    def get_provider(self) -> LLMProvider:
        """Get the provider type.

        Returns:
            LLMProvider.ANTHROPIC
        """
        return LLMProvider.ANTHROPIC

    def is_available(self) -> bool:
        """Check if Anthropic backend is available.

        Returns:
            True if library is installed and API key is configured, False otherwise
        """
        return (
            ANTHROPIC_AVAILABLE and
            bool(self.api_key and self.api_key.startswith("sk-ant-"))
        )
