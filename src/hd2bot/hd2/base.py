from abc import ABC, abstractmethod

from hd2bot.hd2.errors import HD2UnavailableError
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


class HD2Provider(ABC):
    name: str

    @abstractmethod
    async def get_war(self) -> WarStatus: ...

    @abstractmethod
    async def get_planets(self) -> list[Planet]: ...

    async def get_planet(self, index: int) -> Planet | None:
        return next((p for p in await self.get_planets() if p.index == index), None)

    @abstractmethod
    async def get_campaigns(self) -> list[Campaign]: ...

    @abstractmethod
    async def get_major_order(self) -> list[MajorOrder]: ...

    @abstractmethod
    async def get_statistics(self) -> GlobalStatistics: ...

    async def get_dispatches(self) -> list[Dispatch]:
        raise HD2UnavailableError("This provider does not supply dispatches")

    async def get_space_stations(self) -> list[SpaceStation]:
        raise HD2UnavailableError("This provider does not supply space stations")

    async def get_dss_votes(self) -> list[DSSElection]:
        raise HD2UnavailableError("This provider does not supply DSS elections")

    async def get_global_events(self) -> list[GlobalEvent]:
        raise HD2UnavailableError("This provider does not supply global events")

    async def get_episodes(self) -> list[Episode]:
        raise HD2UnavailableError("This provider does not supply episodes")

    async def get_planet_regions(self) -> list[PlanetRegion]:
        raise HD2UnavailableError("This provider does not supply planet regions")

    async def get_special_units(self) -> list[SpecialUnit]:
        raise HD2UnavailableError("This provider does not supply special units")
