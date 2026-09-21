"""Shared NoneBot lifecycle and payload-free SDK logging."""

import asyncio
import logging
from contextlib import asynccontextmanager

from nonebot.log import logger as nonebot_logger

logger = logging.getLogger(__name__)


def safe_nonebot_log(message):
    """Loguru errors can contain credentials, payloads and exception locals."""
    record = message.record
    level = record["level"].no
    if level >= logging.WARNING:
        exception = record.get("exception")
        reason = exception.type.__name__ if exception is not None else None
        logger.log(level, "event=nonebot_diagnostic component=%s level=%s "
                   "function=%s line=%s reason=%s", record["name"], record["level"].name,
                   record["function"], record["line"], reason)


def configure_sdk_logging():
    # Must run before init: config DEBUG logs include app secrets and default
    # SUCCESS event logs include complete user messages.
    nonebot_logger.remove()
    nonebot_logger.add(safe_nonebot_log, level="WARNING", backtrace=False, diagnose=False)


@asynccontextmanager
async def driver_lifespan(driver):
    """Pinned NoneBot 2.5.0 lifecycle bridge for the existing asyncio runner.

    Keep AnyIO task-group enter/exit in the same task. Delay cancellation until
    its graceful shutdown hooks and task-group cleanup have completed.
    """
    cancelled = False
    async with driver._lifespan:
        try:
            yield
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
