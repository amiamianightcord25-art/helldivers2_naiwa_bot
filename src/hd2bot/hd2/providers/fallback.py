import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Generic, TypeVar

from hd2bot.hd2.base import HD2Provider
from hd2bot.hd2.errors import HD2APIError, HD2SchemaError, HD2UnavailableError
from hd2bot.hd2.models import utcnow
from hd2bot.hd2.observation import observations

logger = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass(frozen=True)
class SourcedValue(Generic[T]):
    value: T
    source: str
    fetched_at: datetime = field(default_factory=utcnow)


class FallbackProvider:
    def __init__(self, providers: list[HD2Provider], *, cooldown: float = 30,
                 clock=time.monotonic):
        self.providers, self.cooldown, self.clock = providers, cooldown, clock
        self._retry_at: dict[tuple[str, str], float] = {}

    async def fetch(self, method: str) -> SourcedValue:
        if method not in {"get_war", "get_planets", "get_campaigns", "get_major_order",
                          "get_statistics", "get_dispatches", "get_space_stations",
                          "get_dss_votes",
                          "get_global_events", "get_episodes", "get_planet_regions",
                          "get_special_units"}:
            raise ValueError("Unknown provider resource")
        for index, provider in enumerate(self.providers):
            key = provider.name, method
            if self.clock() < self._retry_at.get(key, 0):
                continue
            reads: dict[str, datetime] = {}
            token = observations.set(reads)
            try:
                value = await getattr(provider, method)()
            except (HD2APIError, KeyError, TypeError, ValueError, AttributeError) as exc:
                reason = type(exc).__name__ if isinstance(exc, HD2APIError) else HD2SchemaError.__name__
                self._retry_at[key] = self.clock() + self.cooldown
                logger.warning("event=provider_failed provider=%s status=failed reason=%s resource=%s",
                               provider.name, reason, method)
                if index + 1 < len(self.providers):
                    logger.warning("event=provider_fallback fallback=%s", self.providers[index + 1].name)
                continue
            finally:
                observations.reset(token)
            self._retry_at.pop(key, None)
            return SourcedValue(value, provider.name, min(reads.values()) if reads else utcnow())
        raise HD2UnavailableError("All configured data providers are unavailable")
