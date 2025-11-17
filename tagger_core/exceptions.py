"""
Custom Exception Hierarchy for AWS TagSense.

This module defines a comprehensive exception hierarchy for better error
handling and debugging throughout the application.
"""


class TagSenseError(Exception):
    """Base exception for all AWS TagSense errors."""
    pass


# AWS-related exceptions
class AWSError(TagSenseError):
    """Base exception for AWS-related errors."""
    pass


class AWSAuthenticationError(AWSError):
    """Raised when AWS authentication fails."""
    pass


class AWSPermissionError(AWSError):
    """Raised when AWS operation fails due to insufficient permissions."""
    pass


class AWSResourceNotFoundError(AWSError):
    """Raised when an AWS resource is not found."""
    pass


class AWSRegionError(AWSError):
    """Raised when there's an issue with AWS region configuration."""
    pass


class AWSScanError(AWSError):
    """Raised when scanning AWS resources fails."""
    pass


class AWSTaggingError(AWSError):
    """Raised when tagging operation fails."""
    pass


# Configuration-related exceptions
class ConfigurationError(TagSenseError):
    """Base exception for configuration errors."""
    pass


class InvalidConfigurationError(ConfigurationError):
    """Raised when configuration is invalid."""
    pass


class MissingConfigurationError(ConfigurationError):
    """Raised when required configuration is missing."""
    pass


# LLM-related exceptions (imported from llm_backends.base)
class LLMError(TagSenseError):
    """Base exception for LLM-related errors."""
    pass


class LLMBackendError(LLMError):
    """Raised when LLM backend initialization fails."""
    pass


# Resource scanner exceptions
class ScannerError(TagSenseError):
    """Base exception for resource scanner errors."""
    pass


class InvalidResourceTypeError(ScannerError):
    """Raised when an invalid resource type is specified."""
    pass


class ScanTimeoutError(ScannerError):
    """Raised when a scan operation times out."""
    pass


# Validation exceptions
class ValidationError(TagSenseError):
    """Base exception for validation errors."""
    pass


class TagValidationError(ValidationError):
    """Raised when tag validation fails."""
    pass


class ResourceValidationError(ValidationError):
    """Raised when resource validation fails."""
    pass
