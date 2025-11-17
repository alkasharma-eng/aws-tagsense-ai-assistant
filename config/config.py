"""
Configuration Management for AWS TagSense.

This module provides centralized configuration management with validation,
environment variable loading, and sensible defaults.
"""

import os
from dataclasses import dataclass, field
from typing import Optional, List
from enum import Enum
import logging


logger = logging.getLogger(__name__)


class LLMProvider(Enum):
    """Supported LLM providers."""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class LogLevel(Enum):
    """Supported log levels."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass
class LLMConfig:
    """Configuration for LLM backends.

    Attributes:
        primary_backend: Primary LLM provider to use
        fallback_backend: Fallback provider if primary fails
        openai_api_key: OpenAI API key
        openai_model: OpenAI model to use
        anthropic_api_key: Anthropic API key
        anthropic_model: Anthropic model to use
        temperature: Generation temperature
        max_tokens: Maximum tokens in response
        enable_cache: Whether to cache responses
        cache_ttl: Cache time-to-live in seconds
    """
    primary_backend: LLMProvider = LLMProvider.OPENAI
    fallback_backend: Optional[LLMProvider] = LLMProvider.ANTHROPIC

    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-3.5-turbo"

    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-3-5-sonnet-20241022"

    temperature: float = 0.3
    max_tokens: int = 2048

    enable_cache: bool = True
    cache_ttl: int = 3600  # 1 hour

    def __post_init__(self):
        """Validate configuration after initialization."""
        # Validate temperature
        if not 0.0 <= self.temperature <= 1.0:
            raise ValueError(f"Temperature must be between 0.0 and 1.0, got {self.temperature}")

        # Validate max_tokens
        if self.max_tokens <= 0:
            raise ValueError(f"max_tokens must be positive, got {self.max_tokens}")

        # Check if primary backend has API key
        if self.primary_backend == LLMProvider.OPENAI and not self.openai_api_key:
            logger.warning("OpenAI API key not configured")

        if self.primary_backend == LLMProvider.ANTHROPIC and not self.anthropic_api_key:
            logger.warning("Anthropic API key not configured")

    @classmethod
    def from_env(cls) -> 'LLMConfig':
        """Load configuration from environment variables.

        Returns:
            LLMConfig instance populated from environment
        """
        # Parse primary backend
        primary_backend_str = os.getenv("LLM_PRIMARY_BACKEND", "openai").lower()
        primary_backend = LLMProvider(primary_backend_str)

        # Parse fallback backend
        fallback_backend_str = os.getenv("LLM_FALLBACK_BACKEND", "anthropic").lower()
        fallback_backend = (
            None if fallback_backend_str == "none"
            else LLMProvider(fallback_backend_str)
        )

        return cls(
            primary_backend=primary_backend,
            fallback_backend=fallback_backend,
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-3.5-turbo"),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
            anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.3")),
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "2048")),
            enable_cache=os.getenv("ENABLE_CACHE", "true").lower() == "true",
            cache_ttl=int(os.getenv("CACHE_TTL", "3600"))
        )


@dataclass
class AWSConfig:
    """Configuration for AWS integration.

    Attributes:
        default_region: Default AWS region
        profile: AWS profile name
        regions: List of regions to scan (for multi-region)
        max_retries: Maximum API retries
        retry_backoff_multiplier: Backoff multiplier for retries
        request_timeout: Request timeout in seconds
    """
    default_region: str = "us-west-2"
    profile: str = "default"
    regions: List[str] = field(default_factory=lambda: ["us-west-2"])

    max_retries: int = 3
    retry_backoff_multiplier: int = 2
    request_timeout: int = 30

    def __post_init__(self):
        """Validate configuration after initialization."""
        if self.max_retries < 0:
            raise ValueError(f"max_retries must be non-negative, got {self.max_retries}")

        if self.request_timeout <= 0:
            raise ValueError(f"request_timeout must be positive, got {self.request_timeout}")

        if not self.regions:
            raise ValueError("At least one region must be specified")

    @classmethod
    def from_env(cls) -> 'AWSConfig':
        """Load configuration from environment variables.

        Returns:
            AWSConfig instance populated from environment
        """
        default_region = os.getenv("AWS_DEFAULT_REGION", "us-west-2")

        # Parse regions list
        regions_str = os.getenv("AWS_REGIONS", default_region)
        regions = [r.strip() for r in regions_str.split(",")]

        return cls(
            default_region=default_region,
            profile=os.getenv("AWS_PROFILE", "default"),
            regions=regions,
            max_retries=int(os.getenv("MAX_RETRIES", "3")),
            retry_backoff_multiplier=int(os.getenv("RETRY_BACKOFF_MULTIPLIER", "2")),
            request_timeout=int(os.getenv("REQUEST_TIMEOUT", "30"))
        )


@dataclass
class AppConfig:
    """Application-level configuration.

    Attributes:
        debug: Enable debug mode
        log_level: Logging level
        log_format: Log message format (text or json)
        conversation_history_length: Max conversation turns to keep
    """
    debug: bool = False
    log_level: LogLevel = LogLevel.INFO
    log_format: str = "text"  # "text" or "json"
    conversation_history_length: int = 10

    def __post_init__(self):
        """Validate configuration after initialization."""
        if self.log_format not in ["text", "json"]:
            raise ValueError(f"log_format must be 'text' or 'json', got {self.log_format}")

        if self.conversation_history_length < 1:
            raise ValueError(
                f"conversation_history_length must be at least 1, got {self.conversation_history_length}"
            )

    @classmethod
    def from_env(cls) -> 'AppConfig':
        """Load configuration from environment variables.

        Returns:
            AppConfig instance populated from environment
        """
        debug = os.getenv("DEBUG", "false").lower() == "true"

        log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
        log_level = LogLevel[log_level_str]

        return cls(
            debug=debug,
            log_level=log_level,
            log_format=os.getenv("LOG_FORMAT", "text").lower(),
            conversation_history_length=int(os.getenv("CONVERSATION_HISTORY_LENGTH", "10"))
        )


@dataclass
class TagSenseConfig:
    """Main configuration for AWS TagSense application.

    This is the top-level configuration that combines all sub-configurations.
    """
    llm: LLMConfig = field(default_factory=LLMConfig)
    aws: AWSConfig = field(default_factory=AWSConfig)
    app: AppConfig = field(default_factory=AppConfig)

    @classmethod
    def from_env(cls) -> 'TagSenseConfig':
        """Load complete configuration from environment variables.

        Returns:
            TagSenseConfig instance with all sub-configs loaded from environment
        """
        return cls(
            llm=LLMConfig.from_env(),
            aws=AWSConfig.from_env(),
            app=AppConfig.from_env()
        )

    def validate(self) -> List[str]:
        """Validate the complete configuration.

        Returns:
            List of validation warnings (empty if all good)
        """
        warnings = []

        # Check LLM API keys
        if self.llm.primary_backend == LLMProvider.OPENAI:
            if not self.llm.openai_api_key:
                warnings.append("OpenAI is the primary backend but OPENAI_API_KEY is not set")

        if self.llm.primary_backend == LLMProvider.ANTHROPIC:
            if not self.llm.anthropic_api_key:
                warnings.append("Anthropic is the primary backend but ANTHROPIC_API_KEY is not set")

        # Check fallback backend
        if self.llm.fallback_backend:
            if self.llm.fallback_backend == LLMProvider.OPENAI and not self.llm.openai_api_key:
                warnings.append("OpenAI is the fallback backend but OPENAI_API_KEY is not set")

            if self.llm.fallback_backend == LLMProvider.ANTHROPIC and not self.llm.anthropic_api_key:
                warnings.append("Anthropic is the fallback backend but ANTHROPIC_API_KEY is not set")

        return warnings

    def summary(self) -> str:
        """Get a human-readable summary of the configuration.

        Returns:
            Configuration summary string
        """
        return f"""
AWS TagSense Configuration:
===========================
LLM:
  Primary Backend: {self.llm.primary_backend.value}
  Fallback Backend: {self.llm.fallback_backend.value if self.llm.fallback_backend else 'None'}
  Model (Primary): {self.llm.openai_model if self.llm.primary_backend == LLMProvider.OPENAI else self.llm.anthropic_model}
  Temperature: {self.llm.temperature}
  Cache Enabled: {self.llm.enable_cache}

AWS:
  Default Region: {self.aws.default_region}
  Profile: {self.aws.profile}
  Regions: {', '.join(self.aws.regions)}

Application:
  Debug Mode: {self.app.debug}
  Log Level: {self.app.log_level.value}
  Log Format: {self.app.log_format}
"""


# Global configuration instance
_config: Optional[TagSenseConfig] = None


def get_config(reload: bool = False) -> TagSenseConfig:
    """Get the global configuration instance.

    Args:
        reload: If True, reload configuration from environment

    Returns:
        TagSenseConfig instance
    """
    global _config

    if _config is None or reload:
        _config = TagSenseConfig.from_env()

        # Log any validation warnings
        warnings = _config.validate()
        if warnings:
            for warning in warnings:
                logger.warning(f"Config warning: {warning}")

    return _config


def reset_config() -> None:
    """Reset the global configuration (useful for testing)."""
    global _config
    _config = None
