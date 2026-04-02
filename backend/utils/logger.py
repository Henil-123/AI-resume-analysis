"""
Logger Setup — Structured logging for the application.
Replaces all print() calls with proper logging.
"""

import logging
import sys


def setup_logging(level=logging.INFO):
    """
    Configure application-wide logging.
    Call this once at app startup.
    """
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers to avoid duplicates
    root_logger.handlers = []
    root_logger.addHandler(console_handler)

    # Suppress noisy third-party loggers
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
    logging.getLogger("torch").setLevel(logging.WARNING)
    logging.getLogger("transformers").setLevel(logging.WARNING)

    return root_logger
