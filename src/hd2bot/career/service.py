"""Lazily load the private connection, coalesce requests and retain source timestamps."""

import asyncio
import time
from dataclasses import replace

from hd2bot.career.client import CareerClient, ConnectionConfig
from hd2bot.career.models import FIELDS, CareerError, CareerStats
from hd2bot.hd2.models import utcnow


class CareerService:
    def __init__(self, settings, *, client=None, clock=time.monotonic):
        self.settings, self.client, self.clock = settings, client, clock
        self._lock = asyncio.Lock()
        self._cached: CareerStats | None = None
        self._expires = 0.0
        self._failure: CareerError | None = None
        self._retry_at = 0.0

    async def query(self) -> CareerStats:
        try:
            async with asyncio.timeout(self.settings.career_timeout), self._lock:
                now = self.clock()
                if self._failure is not None and now < self._retry_at:
                    raise self._failure
                if self._cached is not None and now < self._expires:
                    return replace(self._cached, values=dict(self._cached.values), local_cached=True)
                try:
                    result = await self._load()
                except CareerError as exc:
                    self._failure, self._retry_at = exc, self.clock() + (exc.retry_after or 10)
                    raise
                self._failure = None
                self._cached = result
                # Local TTL governs request frequency; original queried_at is never overwritten.
                self._expires = self.clock() + self.settings.career_cache_ttl
                return replace(result, values=dict(result.values))
        except TimeoutError:
            raise CareerError("timeout") from None

    async def _load(self) -> CareerStats:
        if self.settings.provider == "mock":
            return CareerStats({key: (i + 1) * 10 for i, key in enumerate(FIELDS)}, utcnow(), mock=True)
        if self.client is None:
            if self.settings.career_config_path is None:
                raise CareerError("not_configured")
            config = ConnectionConfig.load(self.settings.career_config_path)
            self.client = CareerClient(config, timeout=self.settings.career_timeout)
        return await self.client.query()

    def _user_client(self):
        if self.settings.provider == "mock":
            raise CareerError("mock_mode")
        if self.client is None:
            if self.settings.career_config_path is None:
                raise CareerError("not_configured")
            config = ConnectionConfig.load(self.settings.career_config_path)
            self.client = CareerClient(config, timeout=self.settings.career_timeout)
        return self.client

    async def challenge(self, user_id: str):
        return await self._user_client().request_user("binding.challenge", user_id)

    async def bind(self, user_id: str, envelope: str):
        return await self._user_client().request_user("binding.import", user_id, envelope)

    async def unbind(self, user_id: str):
        return await self._user_client().request_user("binding.revoke", user_id)

    async def query_user(self, user_id: str) -> CareerStats:
        # Never reuse the legacy owner's cache for a chat user. The server has per-binding caches.
        if self.settings.provider == "mock":
            return await self.query()
        return await self._user_client().request_user("career.query", user_id)

    async def query_shared(self, share_id: str, user_id: str, chat_type: str):
        return await self._user_client().request_user("snapshot.query", user_id, share_id=share_id, chat_type=chat_type)

    async def sharing(self, user_id: str, enabled: bool):
        return await self._user_client().request_user("binding.share" if enabled else "binding.unshare", user_id)

    async def close(self):
        if self.client is not None:
            await self.client.close()
