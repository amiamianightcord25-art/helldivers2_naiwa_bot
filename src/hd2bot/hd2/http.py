"""Async read-only JSON transport with timeouts, throttling, retries and caching."""

import asyncio
import json
import logging
import random
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import aiohttp

from hd2bot.hd2.errors import HD2RateLimitError, HD2SchemaError, HD2UnavailableError
from hd2bot.hd2.observation import record_observation
from hd2bot.services.cache import TTLCache

logger = logging.getLogger(__name__)


def retry_after_seconds(value: str | None) -> float:
    if value:
        try:
            return max(1.0, min(float(value), 86400.0))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                return max(1.0, min((parsed - datetime.now(UTC)).total_seconds(), 86400))
            except (TypeError, ValueError, OverflowError):
                pass
    return 10.0


class JSONHTTPClient:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, *, provider: str,
                 headers: dict[str, str] | None = None, timeout: float = 12,
                 retries: int = 2, min_interval: float = 0.2, sleep=asyncio.sleep):
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.query:
            raise ValueError("API base URL 必须是无认证、无查询参数的 HTTPS 地址")
        self.session, self.base_url, self.provider = session, base_url.rstrip("/"), provider
        self.headers = headers or {}
        self.timeout, self.retries = timeout, retries
        self.min_interval, self.sleep = min_interval, sleep
        self.cache = TTLCache()
        self._gate = asyncio.Lock()
        self._next_request = 0.0
        self._blocked_until = 0.0

    async def get(self, path: str, ttl: float = 20):
        if not path.startswith("/") or path.startswith("//") or "://" in path:
            raise ValueError("API path 必须是站内相对路径")
        result = await self.cache.get(path, lambda: self._request(path), ttl)
        record_observation(self.base_url + path, result.fetched_at)
        return result.value

    async def _throttle(self):
        async with self._gate:
            now = time.monotonic()
            if now < self._blocked_until:
                raise HD2RateLimitError(self._blocked_until - now)
            delay = self._next_request - now
            if delay > 0:
                await self.sleep(delay)
            now = time.monotonic()
            # Another in-flight request may receive 429 while this one waits.
            if now < self._blocked_until:
                raise HD2RateLimitError(self._blocked_until - now)
            self._next_request = now + self.min_interval

    async def _request(self, path: str):
        for attempt in range(self.retries + 1):
            await self._throttle()
            started = time.monotonic()
            status = "network_error"
            delay = None
            try:
                async with self.session.get(
                    self.base_url + path, headers=self.headers,
                    timeout=aiohttp.ClientTimeout(total=self.timeout), allow_redirects=False,
                ) as response:
                    status = str(response.status)
                    if response.status == 429:
                        seconds = retry_after_seconds(response.headers.get("Retry-After"))
                        self._blocked_until = max(self._blocked_until, time.monotonic() + seconds)
                        if attempt >= self.retries or seconds > 5:
                            raise HD2RateLimitError(seconds)
                        delay = seconds
                    elif response.status >= 500:
                        if attempt >= self.retries:
                            raise HD2UnavailableError(f"HTTP {response.status}")
                        delay = 2 ** attempt + random.uniform(0, 0.2)
                    elif response.status != 200:
                        raise HD2UnavailableError(f"HTTP {response.status}")
                    else:
                        body = bytearray()
                        async for chunk in response.content.iter_chunked(65536):
                            body.extend(chunk)
                            if len(body) > 5_000_000:
                                raise HD2SchemaError("Response exceeds size limit")
                        try:
                            return json.loads(body)
                        except (ValueError, UnicodeError) as exc:
                            raise HD2SchemaError("Invalid JSON response") from exc
            except (aiohttp.ClientError, TimeoutError) as exc:
                if attempt >= self.retries:
                    raise HD2UnavailableError(type(exc).__name__) from exc
                delay = 2 ** attempt + random.uniform(0, 0.2)
            finally:
                logger.info("event=api_request provider=%s path=%s latency=%.3f status=%s",
                            self.provider, path.split("?")[0], time.monotonic() - started, status)
            if delay is not None:
                await self.sleep(delay)
