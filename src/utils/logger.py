"""Structured logger using Loguru.

Loguru provides structured, levelled, timestamped logs out of the box.
This module configures a single shared logger instance:
  - Outputs JSON-formatted lines in production (machine-readable)
  - Outputs colourised human-readable lines in development
  - Respects the LOG_LEVEL environment setting

Usage anywhere in the codebase:
    from src.utils.logger import get_logger
    log = get_logger(__name__)
    log.info("Price fetched", route="SYD-LHR", price=450.00)
"""

import sys
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from src.config.settings import Settings

# Remove Loguru's default sink — we configure our own below
logger.remove()


def configure_logger(settings: "Settings") -> None:
    """Configure the global Loguru logger from validated Settings.

    Must be called once, early in main(), after settings are loaded.
    """
    is_production = settings.app_env.value == "production"
    log_level = settings.log_level.value

    if is_production:
        # JSON sink — structured output for Railway/CloudWatch log ingestion
        logger.add(
            sys.stdout,
            level=log_level,
            format=(
                '{{"time":"{time:YYYY-MM-DDTHH:mm:ss.SSSZ}",'
                '"level":"{level}",'
                '"name":"{name}",'
                '"message":"{message}"'
                '{extra}}}'
            ),
            colorize=False,
            backtrace=False,
            diagnose=False,
            serialize=True,  # Loguru native JSON serialization
        )
    else:
        # Human-readable colourised sink for local development
        logger.add(
            sys.stdout,
            level=log_level,
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
                "<level>{message}</level>"
                "{extra}"
            ),
            colorize=True,
            backtrace=True,
            diagnose=True,
        )

    # Also write ERROR+ to a rotating file in both environments
    logger.add(
        "logs/error.log",
        level="ERROR",
        rotation="10 MB",
        retention="30 days",
        compression="gz",
        backtrace=True,
        diagnose=False,  # never write local variable values to disk
    )


def get_logger(name: str):
    """Return a Loguru logger bound to a module name.

    Loguru uses a single global logger; binding a name adds it as
    structured context to every log record from that module.
    """
    return logger.bind(name=name)
