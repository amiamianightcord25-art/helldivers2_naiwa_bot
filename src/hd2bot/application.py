"""Shared dependency lifecycle for CLI and QQ transports."""

from contextlib import asynccontextmanager

import aiohttp

from hd2bot import __version__
from hd2bot.career.service import CareerService
from hd2bot.config import Settings
from hd2bot.hd2.http import JSONHTTPClient
from hd2bot.hd2.providers.fallback import FallbackProvider
from hd2bot.hd2.service import HD2Service


@asynccontextmanager
async def create_service(settings: Settings):
    from hd2bot.hd2.providers.captured import CapturedAPIProvider
    from hd2bot.hd2.providers.community import CommunityProvider
    from hd2bot.hd2.providers.mock import MockProvider

    headers = {"Accept": "application/json", "Accept-Language": "zh-Hans",
               "User-Agent": f"HD2-QQ-Bot/{__version__}"}
    async with aiohttp.ClientSession(trust_env=True) as session:
        captured = CapturedAPIProvider(JSONHTTPClient(
            session, settings.captured_base_url, provider="captured", headers=headers,
            timeout=settings.timeout, retries=settings.retries,
        ), settings)
        community = CommunityProvider(JSONHTTPClient(
            session, settings.community_base_url, provider="community",
            headers={**headers, "X-Super-Client": settings.super_client,
                     "X-Super-Contact": settings.super_contact}, timeout=settings.timeout,
            retries=settings.retries, min_interval=2.1,
        ), settings)
        choices = {"auto": [captured, community], "captured": [captured],
                   "community": [community], "mock": [MockProvider()]}
        yield HD2Service(FallbackProvider(choices[settings.provider]), settings)


@asynccontextmanager
async def create_career_service(settings: Settings):
    service = CareerService(settings)
    try:
        yield service
    finally:
        await service.close()
