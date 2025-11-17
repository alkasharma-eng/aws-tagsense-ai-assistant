"""
OpenAI LLM Backend for AWS TagSense.

This module provides integration with OpenAI's GPT models (GPT-4, GPT-3.5-turbo, etc.)
following the BaseLLMBackend interface.
"""

import os
from typing import List, Optional
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)

from openai import OpenAI, OpenAIError, RateLimitError as OpenAIRateLimitError
from openai.types.chat import ChatCompletionMessageParam

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


class OpenAIBackend(BaseLLMBackend):
    """OpenAI GPT backend implementation.

    Supports GPT-4, GPT-4-Turbo, and GPT-3.5-Turbo models with automatic retries,
    rate limit handling, and error recovery.

    Example:
        ```python
        backend = OpenAIBackend(
            model="gpt-4",
            temperature=0.3,
            api_key=os.getenv("OPENAI_API_KEY")
        )

        response = backend.generate_simple(
            prompt="Explain AWS tagging best practices",
            system_prompt="You are a cloud compliance expert"
        )
        print(response)
        ```
    """

    SUPPORTED_MODELS = [
        "gpt-4",
        "gpt-4-turbo",
        "gpt-4-turbo-preview",
        "gpt-3.5-turbo",
        "gpt-3.5-turbo-16k"
    ]

    def __init__(
        self,
        model: str = "gpt-3.5-turbo",
        temperature: float = 0.3,
        max_tokens: int = 2048,
        api_key: Optional[str] = None
    ):
        """Initialize OpenAI backend.

        Args:
            model: OpenAI model to use (default: gpt-3.5-turbo)
            temperature: Temperature for generation (0.0-1.0)
            max_tokens: Maximum tokens in response
            api_key: OpenAI API key (if None, loads from OPENAI_API_KEY env var)

        Raises:
            ValueError: If API key is missing or model is not supported
        """
        # Load API key from environment if not provided
        if api_key is None:
            api_key = os.getenv("OPENAI_API_KEY")

        super().__init__(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key
        )

        # Initialize OpenAI client
        self.client = OpenAI(api_key=self.api_key)

    def _validate_config(self) -> None:
        """Validate OpenAI configuration.

        Raises:
            ValueError: If API key is missing or model is not supported
        """
        if not self.api_key:
            raise ValueError(
                "OpenAI API key is required. Set OPENAI_API_KEY environment variable "
                "or provide it as a parameter."
            )

        if not self.api_key.startswith("sk-"):
            raise ValueError(
                "Invalid OpenAI API key format. API keys should start with 'sk-'"
            )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(OpenAIRateLimitError),
        reraise=True
    )
    def generate(
        self,
        messages: List[LLMMessage],
        **kwargs
    ) -> LLMResponse:
        """Generate a response using OpenAI's API.

        This method includes automatic retry logic with exponential backoff
        for rate limit errors (429).

        Args:
            messages: List of conversation messages
            **kwargs: Additional OpenAI-specific parameters (e.g., top_p, frequency_penalty)

        Returns:
            LLMResponse containing the generated content and metadata

        Raises:
            RateLimitError: If rate limit exceeded after retries
            AuthenticationError: If API key is invalid
            ModelNotFoundError: If the specified model doesn't exist
            LLMError: For other API errors
        """
        try:
            # Convert LLMMessage objects to OpenAI format
            openai_messages: List[ChatCompletionMessageParam] = [
                {"role": msg.role, "content": msg.content}
                for msg in messages
            ]

            # Make API call
            response = self.client.chat.completions.create(
                model=self.model,
                messages=openai_messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                **kwargs
            )

            # Extract response content
            content = response.choices[0].message.content

            # Build usage information
            usage = None
            if response.usage:
                usage = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens
                }

            return LLMResponse(
                content=content,
                model=response.model,
                provider=LLMProvider.OPENAI,
                usage=usage,
                cached=False
            )

        except OpenAIRateLimitError as e:
            raise RateLimitError(
                f"OpenAI rate limit exceeded: {str(e)}. "
                "Please wait a moment and try again."
            )

        except OpenAIError as e:
            error_message = str(e)

            # Check for specific error types
            if "invalid_api_key" in error_message or "authentication" in error_message.lower():
                raise AuthenticationError(
                    f"OpenAI authentication failed: {error_message}. "
                    "Please check your API key."
                )

            if "model_not_found" in error_message or f"model '{self.model}'" in error_message:
                raise ModelNotFoundError(
                    f"Model '{self.model}' not found. "
                    f"Supported models: {', '.join(self.SUPPORTED_MODELS)}"
                )

            # Generic OpenAI error
            raise LLMError(f"OpenAI API error: {error_message}")

        except Exception as e:
            raise LLMError(f"Unexpected error during OpenAI API call: {str(e)}")

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
            **kwargs: Additional OpenAI parameters

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
            LLMProvider.OPENAI
        """
        return LLMProvider.OPENAI

    def is_available(self) -> bool:
        """Check if OpenAI backend is available.

        Returns:
            True if API key is configured, False otherwise
        """
        return bool(self.api_key and self.api_key.startswith("sk-"))

    def list_models(self) -> List[str]:
        """List available OpenAI models.

        Returns:
            List of model identifiers

        Note:
            This queries the OpenAI API to get the actual available models.
            For a static list, use SUPPORTED_MODELS class variable.
        """
        try:
            models = self.client.models.list()
            return [model.id for model in models.data if "gpt" in model.id]
        except Exception:
            # Fallback to supported models if API call fails
            return self.SUPPORTED_MODELS
