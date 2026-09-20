"""Monotonic TTL cache with per-key request coalescing and bounded stale fallback."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Generic, TypeVar

from hd2bot.hd2.errors import HD2APIError
from hd2bot.hd2.models import utcnow

T = TypeVar("T")


@dataclass(frozen=True)
class CacheResult(Generic[T]):
    value: T
    fetched_at: datetime
    stale: bool = False


class TTLCache:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic, failure_ttl: float = 5):
        self.clock = clock
        self.failure_ttl = failure_ttl
        self._entries: dict[str, tuple[float, CacheResult]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._failures: dict[str, tuple[float, HD2APIError]] = {}

    async def get(self, key: str, loader: Callable[[], Awaitable[T]], ttl: float,
                  stale_ttl: float = 0,
                  timestamp: Callable[[T], datetime] | None = None) -> CacheResult[T]:
        async with self._locks.setdefault(key, asyncio.Lock()):
            now = self.clock()
            entry = self._entries.get(key)
            if entry and now < entry[0]:
                return entry[1]
            failure = self._failures.get(key)
            if failure and now < failure[0]:
                if entry and now < entry[0] + stale_ttl:
                    return replace(entry[1], stale=True)
                raise failure[1]
            try:
                value = await loader()
            except HD2APIError as exc:
                self._failures[key] = (self.clock() + self.failure_ttl, exc)
                if entry and self.clock() < entry[0] + stale_ttl:
                    return replace(entry[1], stale=True)
                raise
            now = utcnow()
            fetched_at = timestamp(value) if timestamp is not None else now
            age = max(0.0, (now - fetched_at).total_seconds())
            result = CacheResult(value, fetched_at, stale=timestamp is not None and 0 < ttl <= age)
            # A cached provider response retains its original freshness budget.
            # Keep negative remaining TTL so stale fallback cannot extend it.
            self._entries[key] = (self.clock() + ttl - age, result)
            self._failures.pop(key, None)
            return result

    def clear(self) -> None:
        self._entries.clear()
        self._failures.clear()
