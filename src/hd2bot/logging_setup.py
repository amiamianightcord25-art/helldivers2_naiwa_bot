"""Redacted rotating application logs. Never log HTTP bodies or request headers."""

import logging
import re
from logging.handlers import RotatingFileHandler

from hd2bot.config import Settings


class SensitiveFilter(logging.Filter):
    def __init__(self, secrets: tuple[str, ...] = ()):
        super().__init__()
        self.secrets = tuple(value for value in secrets if value)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for value in self.secrets:
            message = message.replace(value, "<REDACTED>")
        message = re.sub(
            r"(?i)(authorization|cookie|(?:app|client)[_-]?secret|access[_-]?token|session[_-]?token|token)"
            r"([\s\"']*[:=][\s\"']*)([^\r\n,}]+)",
            r"\1\2<REDACTED>", message,
        )
        message = re.sub(r"(?i)\b(Bearer|QQBot)\s+[^\s\"',}]+", r"\1 <REDACTED>", message)
        message = re.sub(r"(?:HD2B1\.|HD2v1:)[A-Za-z0-9_-]+", "<BINDING_REDACTED>", message)
        record.msg, record.args = message, ()
        # Exception text can carry full URLs or credentials from a third-party SDK.
        record.exc_info = None
        record.exc_text = None
        return True


def configure_logging(settings: Settings) -> None:
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handlers = [logging.StreamHandler(), RotatingFileHandler(
        settings.log_dir / "bot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )]
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(SensitiveFilter((settings.qq_app_secret,)))
    logging.basicConfig(level=settings.log_level, handlers=handlers, force=True)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
