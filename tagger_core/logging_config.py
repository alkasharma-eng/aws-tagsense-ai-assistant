"""
Structured Logging Configuration for AWS TagSense.

This module provides structured logging with JSON format support for
production environments and human-readable format for development.
"""

import logging
import json
import sys
from datetime import datetime
from typing import Dict, Any
from enum import Enum


class LogFormat(Enum):
    """Supported log formats."""
    TEXT = "text"
    JSON = "json"


class JSONFormatter(logging.Formatter):
    """Custom formatter that outputs logs in JSON format.

    This is ideal for production environments where logs are ingested by
    centralized logging systems (CloudWatch, ELK, Datadog, etc.).
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON.

        Args:
            record: Log record to format

        Returns:
            JSON string representation of the log
        """
        log_data = {
            "timestamp": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add extra fields if present
        if hasattr(record, "extra_fields"):
            log_data.update(record.extra_fields)

        return json.dumps(log_data)


class TextFormatter(logging.Formatter):
    """Custom formatter for human-readable text logs.

    This is ideal for development and debugging.
    """

    # Color codes for terminal output
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
        'RESET': '\033[0m'       # Reset
    }

    def __init__(self, use_colors: bool = True):
        """Initialize text formatter.

        Args:
            use_colors: Whether to use colored output (default: True)
        """
        super().__init__(
            fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        self.use_colors = use_colors

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as colored text.

        Args:
            record: Log record to format

        Returns:
            Formatted log string
        """
        if self.use_colors and sys.stdout.isatty():
            # Add color codes
            level_color = self.COLORS.get(record.levelname, '')
            reset = self.COLORS['RESET']
            record.levelname = f"{level_color}{record.levelname}{reset}"

        return super().format(record)


def setup_logging(
    level: str = "INFO",
    format_type: str = "text",
    log_file: str = None
) -> None:
    """Configure application-wide logging.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        format_type: Log format ("text" or "json")
        log_file: Optional file path for log output

    Example:
        ```python
        # Development setup (colored text to console)
        setup_logging(level="DEBUG", format_type="text")

        # Production setup (JSON to file)
        setup_logging(
            level="INFO",
            format_type="json",
            log_file="/var/log/tagsense/app.log"
        )
        ```
    """
    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))

    # Remove existing handlers
    root_logger.handlers.clear()

    # Choose formatter
    if format_type.lower() == "json":
        formatter = JSONFormatter()
    else:
        formatter = TextFormatter(use_colors=True)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File handler (if specified)
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # Set logging levels for noisy libraries
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)

    logging.info(f"Logging configured: level={level}, format={format_type}")


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance with the specified name.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Logger instance
    """
    return logging.getLogger(name)


class StructuredLogger:
    """Wrapper for logging with structured fields.

    This class makes it easy to add structured context to log messages
    for better searchability in log aggregation systems.

    Example:
        ```python
        logger = StructuredLogger("tagsense.scanner")

        logger.info(
            "EC2 scan complete",
            extra={
                "region": "us-east-1",
                "instance_count": 42,
                "untagged_count": 5
            }
        )
        ```
    """

    def __init__(self, name: str):
        """Initialize structured logger.

        Args:
            name: Logger name
        """
        self.logger = logging.getLogger(name)

    def _log(
        self,
        level: int,
        message: str,
        extra: Dict[str, Any] = None,
        exc_info: bool = False
    ) -> None:
        """Internal logging method with structured fields.

        Args:
            level: Log level
            message: Log message
            extra: Extra structured fields
            exc_info: Include exception information
        """
        record = self.logger.makeRecord(
            self.logger.name,
            level,
            "(structured)",
            0,
            message,
            (),
            exc_info=exc_info if exc_info else None
        )

        if extra:
            record.extra_fields = extra

        self.logger.handle(record)

    def debug(self, message: str, extra: Dict[str, Any] = None) -> None:
        """Log debug message with structured fields."""
        self._log(logging.DEBUG, message, extra)

    def info(self, message: str, extra: Dict[str, Any] = None) -> None:
        """Log info message with structured fields."""
        self._log(logging.INFO, message, extra)

    def warning(self, message: str, extra: Dict[str, Any] = None) -> None:
        """Log warning message with structured fields."""
        self._log(logging.WARNING, message, extra)

    def error(self, message: str, extra: Dict[str, Any] = None, exc_info: bool = False) -> None:
        """Log error message with structured fields."""
        self._log(logging.ERROR, message, extra, exc_info=exc_info)

    def critical(self, message: str, extra: Dict[str, Any] = None, exc_info: bool = False) -> None:
        """Log critical message with structured fields."""
        self._log(logging.CRITICAL, message, extra, exc_info=exc_info)
