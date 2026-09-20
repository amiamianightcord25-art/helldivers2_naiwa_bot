from dataclasses import dataclass
from datetime import datetime
from typing import Generic, TypeVar

from hd2bot.config import Settings
from hd2bot.hd2.models import (
    Campaign,
    Dispatch,
    DSSElection,
    Episode,
    GlobalEvent,
    GlobalStatistics,
    MajorOrder,
    Planet,
    PlanetRegion,
    SpaceStation,
    SpecialUnit,
    WarStatus,
)
from hd2bot.hd2.providers.fallback import FallbackProvider
from hd2bot.services.cache import TTLCache

T = TypeVar("T")


@dataclass(frozen=True)
class DataResult(Generic[T]):
    value: T
    source: str
    fetched_at: datetime
    stale: bool = False


class HD2Service:
    def __init__(self, provider: FallbackProvider, settings: Settings, cache: TTLCache | None = None):
        self.provider, self.settings = provider, settings
        self.cache = cache or TTLCache()

    async def _get(self, method: str, ttl: float) -> DataResult:
        cached = await self.cache.get(method, lambda: self.provider.fetch(method), ttl,
                                      self.settings.stale_ttl,
                                      timestamp=lambda sourced: sourced.fetched_at)
        return DataResult(cached.value.value, cached.value.source, cached.fetched_at, cached.stale)

    async def get_war(self) -> DataResult[WarStatus]:
        return await self._get("get_war", self.settings.cache_ttl)

    async def get_planets(self) -> DataResult[list[Planet]]:
        return await self._get("get_planets", self.settings.cache_ttl)

    async def get_campaigns(self) -> DataResult[list[Campaign]]:
        return await self._get("get_campaigns", self.settings.cache_ttl)

    async def get_major_order(self) -> DataResult[list[MajorOrder]]:
        return await self._get("get_major_order", self.settings.order_ttl)

    async def get_statistics(self) -> DataResult[GlobalStatistics]:
        return await self._get("get_statistics", self.settings.statistics_ttl)

    async def get_dispatches(self) -> DataResult[list[Dispatch]]:
        return await self._get("get_dispatches", self.settings.order_ttl)

    async def get_space_stations(self) -> DataResult[list[SpaceStation]]:
        return await self._get("get_space_stations", self.settings.cache_ttl)

    async def get_dss_votes(self) -> DataResult[list[DSSElection]]:
        return await self._get("get_dss_votes", self.settings.cache_ttl)

    async def get_global_events(self) -> DataResult[list[GlobalEvent]]:
        return await self._get("get_global_events", self.settings.cache_ttl)

    async def get_episodes(self) -> DataResult[list[Episode]]:
        return await self._get("get_episodes", self.settings.order_ttl)

    async def get_planet_regions(self) -> DataResult[list[PlanetRegion]]:
        return await self._get("get_planet_regions", self.settings.cache_ttl)

    async def get_special_units(self) -> DataResult[list[SpecialUnit]]:
        return await self._get("get_special_units", self.settings.cache_ttl)
