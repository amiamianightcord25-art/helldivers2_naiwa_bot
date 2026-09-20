"""Parse the public, read-only Arrowhead routes confirmed by the captures.

No game session, captured header values or player credentials are replayed.
"""

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import monotonic

from hd2bot.hd2.base import HD2Provider
from hd2bot.hd2.errors import HD2APIError, HD2SchemaError
from hd2bot.hd2.models import (
    SPECIAL_UNIT_CATALOG,
    Campaign,
    Dispatch,
    DSSElection,
    DSSVoteOption,
    Episode,
    EpisodePhase,
    EpisodeReward,
    Faction,
    GlobalEvent,
    GlobalStatistics,
    MajorOrder,
    OrderTask,
    Planet,
    PlanetEvent,
    PlanetRegion,
    Reward,
    SpaceStation,
    SpecialUnit,
    TacticalAction,
    TacticalCost,
    WarStatus,
    utcnow,
)
from hd2bot.hd2.observation import observations, record_observation
from hd2bot.hd2.parsing import (
    date,
    integer,
    number,
    object_value,
    progress,
    require_list,
    require_object,
    sum_known,
    text,
)
from hd2bot.services.concurrency import gather_cancel_on_error
from hd2bot.services.localization import metadata_for

PATHS = {
    "war_id": "/api/WarSeason/current/WarID",
    "war_info": "/api/WarSeason/{war_id}/WarInfo",
    "status": "/api/WarSeason/{war_id}/Status",
    "assignments": "/api/v2/Assignment/War/{war_id}",
    "statistics": "/api/Stats/war/{war_id}/summary",
    "episodes": "/api/Episode/{war_id}",
    "effects": "/api/WarSeason/GalacticWarEffects",
    "election": "/api/ElectionV2/{war_id}/{election_id}",
}


@dataclass
class _Response:
    payload: object
    observed_at: datetime
    expires: float


def _relative_date(anchor: datetime, seconds) -> datetime | None:
    value = number(seconds)
    if value is not None:
        try:
            return anchor + timedelta(seconds=value)
        except OverflowError:
            pass
    return None


def _war_date(response: _Response, value) -> datetime | None:
    """A nonzero war-clock deadline anchored to the actual Status sample."""
    war_time, value = number(response.payload.get("time")), number(value)
    if war_time is None or war_time < 0 or value is None or value <= 0:
        return None
    return _relative_date(response.observed_at, value - war_time)


def _ids(value) -> tuple[int, ...]:
    result = []
    for raw in require_list(value):
        item = integer(raw)
        if item is None or item < 0:
            raise HD2SchemaError("Invalid response identifier")
        result.append(item)
    return tuple(result)


def _indexed(rows: list, field: str) -> dict[int, dict]:
    result = {}
    for value in rows:
        row = require_object(value, (field,))
        index = integer(row[field])
        if index is None or index < 0 or index in result:
            raise HD2SchemaError("Invalid or duplicate response identifier")
        result[index] = row
    return result


class CapturedAPIProvider(HD2Provider):
    name = "captured"

    def __init__(self, http, settings):
        self.http = http
        self.settings = settings
        self.paths = {**PATHS, **getattr(settings, "captured_paths", {})}
        self._cache: dict[str, _Response] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def _read(self, path: str, ttl: float) -> _Response:
        cached = self._cache.get(path)
        if cached is not None and cached.expires > monotonic():
            return cached
        async with self._locks.setdefault(path, asyncio.Lock()):
            cached = self._cache.get(path)
            if cached is not None and cached.expires > monotonic():
                return cached
            # Own the freshness timestamp here; a second cache could return an
            # older relative deadline while giving it a new observed_at value.
            # Raw resources record their own timestamps below; static directory
            # and war-ID fetches must not define dynamic snapshot freshness.
            token = observations.set(None)
            try:
                payload = await self.http.get(path, ttl=0)
            finally:
                observations.reset(token)
            response = _Response(payload, utcnow(), monotonic() + ttl)
            self._cache[path] = response
            return response

    async def _war_id(self) -> int:
        response = await self._read(self.paths["war_id"], self.settings.static_ttl)
        value = integer(require_object(response.payload, ("id",))["id"])
        if value is None or value < 0:
            raise HD2SchemaError("Invalid current war identifier")
        return value

    async def _resource(self, name: str, ttl: float) -> _Response:
        war_id = await self._war_id()
        response = await self._read(self.paths[name].format(war_id=war_id), ttl)
        if name in {"status", "assignments", "statistics", "episodes"}:
            record_observation("captured:" + name, response.observed_at)
        return response

    async def _status(self) -> _Response:
        response = await self._resource("status", self.settings.cache_ttl)
        require_list(require_object(response.payload, ("planetStatus",))["planetStatus"])
        return response

    async def _info(self) -> _Response:
        response = await self._resource("war_info", self.settings.static_ttl)
        require_list(require_object(response.payload, ("planetInfos",))["planetInfos"])
        return response

    async def get_war(self) -> WarStatus:
        status_response, info_response = await gather_cancel_on_error(self._status(), self._info())
        status, info = status_response.payload, info_response.payload
        statuses = _indexed(status["planetStatus"], "index")
        events = status.get("globalEvents", [])
        messages = []
        for event in require_list(events):
            row = require_object(event)
            message = text(row.get("message")) or text(row.get("title"))
            if message:
                messages.append(message)
        return WarStatus(
            war_id=await self._war_id(),
            started_at=date(info.get("startDate")),
            ended_at=date(info.get("endDate")),
            war_time=number(status.get("time")),
            players=sum_known(integer(row.get("players")) for row in statuses.values()),
            impact_multiplier=number(status.get("impactMultiplier")),
            events=tuple(messages),
        )

    def _event(self, row: dict, status: dict, observed_at: datetime) -> PlanetEvent:
        war_time = number(status.get("time"))
        start, end = number(row.get("startTime")), number(row.get("expireTime"))
        return PlanetEvent(
            id=integer(row.get("id")),
            event_type=integer(row.get("eventType")),
            faction=Faction.parse(row.get("race")),
            health=number(row.get("health")),
            max_health=number(row.get("maxHealth")),
            starts_at=_relative_date(observed_at, start - war_time)
            if start is not None and war_time is not None else None,
            ends_at=_relative_date(observed_at, end - war_time)
            if end is not None and war_time is not None else None,
            progress=progress(row.get("health"), row.get("maxHealth")),
        )

    async def get_planets(self) -> list[Planet]:
        status_response, info_response = await gather_cancel_on_error(self._status(), self._info())
        status, info = status_response.payload, info_response.payload
        statuses = _indexed(status["planetStatus"], "index")
        infos = _indexed(info["planetInfos"], "index")
        events: dict[int, dict] = {}
        for value in require_list(status.get("planetEvents", [])):
            event = require_object(value, ("planetIndex",))
            index = integer(event["planetIndex"])
            if index is None or index < 0:
                raise HD2SchemaError("Invalid event planet identifier")
            if index not in events or integer(event.get("eventType")) == 1:
                events[index] = event
        planets = []
        for index in sorted(infos.keys() | statuses.keys()):
            current, static = statuses.get(index, {}), infos.get(index, {})
            metadata = metadata_for(index)
            position = object_value(current.get("position") or static.get("position"))
            x, y = number(position.get("x")), number(position.get("y"))
            faction = Faction.parse(current.get("owner"))
            liberation = progress(current.get("health"), static.get("maxHealth"))
            # Human HP represents retained control, not unfinished liberation.
            if faction in {Faction.HUMANS, Faction.UNKNOWN}:
                liberation = None
            planets.append(Planet(
                index=index,
                name=text(metadata.get("name")) or f"星球 #{index}",
                english_name=text(metadata.get("english_name")),
                aliases=tuple(metadata.get("aliases", ())),
                sector=text(metadata.get("sector")),
                faction=faction,
                health=number(current.get("health")),
                max_health=number(static.get("maxHealth")),
                liberation=liberation,
                players=integer(current.get("players")),
                regen_rate=number(current.get("regenPerSecond")),
                event=self._event(events[index], status, status_response.observed_at)
                if index in events else None,
                biome=text(metadata.get("biome")),
                hazards=tuple(metadata.get("hazards", ())),
                position=(x, y) if x is not None and y is not None else None,
                waypoints=tuple(value for raw in require_list(static.get("waypoints", []))
                                if (value := integer(raw)) is not None),
                disabled=static.get("disabled") is True,
            ))
        return planets

    async def get_campaigns(self) -> list[Campaign]:
        response, planets = await gather_cancel_on_error(self._status(), self.get_planets())
        rows = require_list(require_object(response.payload, ("campaigns",))["campaigns"])
        planet_index = {planet.index: planet for planet in planets}
        campaigns = []
        for index, row in _indexed(rows, "id").items():
            target = integer(row.get("planetIndex"))
            if target is None or target < 0:
                raise HD2SchemaError("Invalid campaign planet identifier")
            planet = planet_index.get(target)
            if planet is None:
                metadata = metadata_for(target)
                planet = Planet(
                    index=target,
                    name=text(metadata.get("name")) or f"星球 #{target}",
                    english_name=text(metadata.get("english_name")),
                    aliases=tuple(metadata.get("aliases", ())),
                )
            campaigns.append(Campaign(
                id=index, planet=planet,
                type=integer(row.get("type")), count=integer(row.get("count")),
                faction=Faction.parse(row.get("race")),
            ))
        return campaigns

    @staticmethod
    def _task(row: dict, task_progress) -> OrderTask:
        values = require_list(row.get("values", []))
        value_types = require_list(row.get("valueTypes", []))
        if any(number(value) is None for value in values):
            raise HD2SchemaError("Invalid task values")
        if any(integer(value) is None for value in value_types):
            raise HD2SchemaError("Invalid task parameter types")
        task_type = integer(row.get("type"))
        parameters = dict(zip(value_types, values))
        # Only infer this observed liberation task; preserve other task schemas.
        liberation = task_type == 11 and len(values) == len(value_types)
        return OrderTask(
            type=task_type, values=tuple(values), value_types=tuple(value_types),
            progress=number(task_progress),
            target=number(parameters.get(3)) if liberation else None,
            planet_index=integer(parameters.get(12)) if liberation else None,
            description=text(row.get("description")),
        )

    async def get_major_order(self) -> list[MajorOrder]:
        response = await self._resource("assignments", self.settings.order_ttl)
        orders = []
        for order_id, row in _indexed(require_list(response.payload), "id32").items():
            setting = require_object(row.get("setting"), ("tasks",))
            task_rows = require_list(setting["tasks"])
            progresses = require_list(row.get("progress", []))
            tasks = [self._task(require_object(task), progresses[index]
                                if index < len(progresses) else None)
                     for index, task in enumerate(task_rows)]
            raw_rewards = require_list(setting.get("rewards", []))
            if not raw_rewards and setting.get("reward") is not None:
                raw_rewards = [setting["reward"]]
            rewards = []
            for raw in raw_rewards:
                reward = require_object(raw)
                rewards.append(Reward(type=integer(reward.get("type")),
                                      amount=integer(reward.get("amount"))))
            orders.append(MajorOrder(
                id=order_id, title=text(setting.get("overrideTitle")),
                description=text(setting.get("taskDescription")),
                briefing=text(setting.get("overrideBrief")), tasks=tasks, rewards=rewards,
                expires_at=_relative_date(response.observed_at, row.get("expiresIn")),
            ))
        return orders

    async def get_dispatches(self) -> list[Dispatch]:
        war_id = await self._war_id()
        # Keep a stable cache key: fromTimestamp changes with the war clock.
        key = f"dispatches:{war_id}"
        async with self._locks.setdefault(key, asyncio.Lock()):
            cached = self._cache.get(key)
            if cached is not None and cached.expires > monotonic():
                record_observation("captured:dispatches", cached.observed_at)
                return list(cached.payload)
            status = await self._status()
            war_time = number(status.payload.get("time"))
            if war_time is not None and war_time < 0:
                war_time = None
            since = max(0, int(war_time) - 7 * 86400) if war_time is not None else 0
            # fromTimestamp=0 returns oldest-first. A recent window is essential.
            limit = 1024 if war_time is not None else 4096
            path = f"/api/NewsFeed/{war_id}?maxEntries={limit}&fromTimestamp={since}"
            payload = await self.http.get(path, ttl=0)
            now = utcnow()
            items = []
            for dispatch_id, row in _indexed(require_list(payload), "id").items():
                message = text(row.get("message"))
                if message is None:
                    raise HD2SchemaError("Dispatch message is missing")
                published = number(row.get("published"))
                # WarInfo.startDate is not the runtime war-clock origin. Use the
                # same sampled Status.time anchor as planet event deadlines.
                published_at = (
                    _relative_date(status.observed_at, published - war_time)
                    if published is not None and published >= 0 and war_time is not None
                    else None
                )
                items.append(Dispatch(dispatch_id, message, published_at))
            items.sort(key=lambda item: item.id, reverse=True)
            observed_at = min(status.observed_at, now)
            self._cache[key] = _Response(items, observed_at, monotonic() + self.settings.order_ttl)
            record_observation("captured:dispatches", observed_at)
            return list(items)

    async def get_global_events(self) -> list[GlobalEvent]:
        response = await self._status()
        rows = require_list(require_object(response.payload, ("globalEvents",))["globalEvents"])
        return [GlobalEvent(
            id=event_id, title=text(row.get("title")), message=text(row.get("message")),
            faction=Faction.parse(row.get("race")),
            planet_indices=_ids(row.get("planetIndices", [])),
            effect_ids=_ids(row.get("effectIds", [])),
            assignment_id=integer(row.get("assignmentId32")),
            expires_at=_war_date(response, row.get("expireTime")),
        ) for event_id, row in _indexed(rows, "eventId").items()]

    async def get_space_stations(self) -> list[SpaceStation]:
        status = await self._status()
        rows = require_list(require_object(status.payload, ("spaceStations",))["spaceStations"])
        stations = []
        war_id = await self._war_id()
        for station_id, summary in _indexed(rows, "id32").items():
            response = await self._read(
                f"/api/SpaceStation/{war_id}/{station_id}", self.settings.cache_ttl,
            )
            record_observation(f"captured:station:{station_id}", response.observed_at)
            row = require_object(response.payload, ("id32", "tacticalActions"))
            if integer(row["id32"]) != station_id:
                raise HD2SchemaError("Space station identifier mismatch")
            actions = []
            for action_id, action in _indexed(require_list(row["tacticalActions"]), "id32").items():
                costs = []
                for raw in require_list(action.get("cost", [])):
                    cost = require_object(raw)
                    costs.append(TacticalCost(
                        item_id=integer(cost.get("itemMixId")),
                        current=number(cost.get("currentValue")),
                        target=number(cost.get("targetValue")),
                        donation_limit=integer(cost.get("maxDonationAmount")),
                        donation_period_seconds=integer(cost.get("maxDonationPeriodSeconds")),
                    ))
                actions.append(TacticalAction(
                    id=action_id, name=text(action.get("name")),
                    status=integer(action.get("status")),
                    expires_at=_war_date(status, action.get("statusExpireAtWarTimeSeconds")),
                    description=text(action.get("description")),
                    strategic_description=text(action.get("strategicDescription")), costs=costs,
                ))
            election_id = text(row.get("currentElectionId"))
            if election_id is not None and not re.fullmatch(r"[A-Za-z0-9-]{1,64}", election_id):
                raise HD2SchemaError("Invalid DSS election identifier")
            election = None
            if election_id:
                try:
                    election_response = await self._read(
                        self.paths["election"].format(
                            war_id=war_id, election_id=election_id,
                        ), self.settings.cache_ttl,
                    )
                    record_observation(
                        f"captured:election:{election_id}", election_response.observed_at,
                    )
                    election = self._parse_election(election_id, election_response.payload)
                except HD2APIError:
                    # Election rotation can race the station snapshot. Keep the
                    # station itself usable and expose votes only when complete.
                    pass
            stations.append(SpaceStation(
                id=station_id, planet_index=integer(row.get("planetIndex")),
                flags=integer(row.get("flags")),
                election_ends_at=_war_date(status, row.get("currentElectionEndWarTime")),
                tactical_actions=actions,
                election_id=election_id,
                election=election,
            ))
        return stations

    @staticmethod
    def _parse_election(election_id: str, payload) -> DSSElection:
        row = require_object(payload, ("options",))
        options = []
        seen = set()
        for raw in require_list(row["options"]):
            option = require_object(raw, ("metaId", "count"))
            meta_id = integer(option["metaId"])
            count = integer(option["count"])
            if (meta_id is None or meta_id < 0 or count is None or count < 0
                    or meta_id in seen):
                raise HD2SchemaError("Invalid DSS election option")
            seen.add(meta_id)
            options.append(DSSVoteOption(
                meta_id=meta_id, count=count,
                text=text(option.get("text")), id=text(option.get("id")),
            ))
        return DSSElection(
            id=election_id,
            context=integer(row.get("context")),
            status=integer(row.get("status")),
            options=options,
        )

    async def get_dss_votes(self) -> list[DSSElection]:
        stations = await self.get_space_stations()
        return [station.election for station in stations if station.election is not None]

    @staticmethod
    def _episode_rewards(values) -> list[EpisodeReward]:
        result = []
        for value in require_list(values):
            row = require_object(value)
            result.append(EpisodeReward(integer(row.get("mixId")), integer(row.get("amount"))))
        return result

    async def get_episodes(self) -> list[Episode]:
        status, response = await gather_cancel_on_error(
            self._status(), self._resource("episodes", self.settings.order_ttl),
        )
        rows = require_list(require_object(response.payload, ("episodes",))["episodes"])
        episodes = []
        for episode_id, row in _indexed(rows, "id32").items():
            phases = []
            for phase_id, phase in _indexed(require_list(row.get("phases", [])), "id32").items():
                phases.append(EpisodePhase(
                    id=phase_id, status=integer(phase.get("status")),
                    intro_title=text(phase.get("introTitle")),
                    intro_message=text(phase.get("introMessage")),
                    outro_title=text(phase.get("outroTitle")),
                    outro_message=text(phase.get("outroMessage")),
                    rewards=self._episode_rewards(phase.get("rewards", [])),
                ))
            episodes.append(Episode(
                id=episode_id, title=text(row.get("title")), description=text(row.get("description")),
                intro_message=text(row.get("introMessage")), outro_message=text(row.get("outroMessage")),
                faction=Faction.parse(row.get("race")), status=integer(row.get("status")),
                starts_at=_war_date(status, row.get("startWarTime")),
                ends_at=_war_date(status, row.get("endWarTime")), phases=phases,
                rewards=self._episode_rewards(row.get("rewards", [])),
            ))
        return episodes

    async def get_planet_regions(self) -> list[PlanetRegion]:
        status, info = await gather_cancel_on_error(self._status(), self._info())
        rows = require_list(require_object(status.payload, ("planetRegions",))["planetRegions"])
        static_rows = require_list(require_object(info.payload, ("planetRegions",))["planetRegions"])

        def indexed_regions(values):
            result = {}
            for value in values:
                row = require_object(value, ("planetIndex", "regionIndex"))
                planet, region = integer(row["planetIndex"]), integer(row["regionIndex"])
                if planet is None or region is None or planet < 0 or region < 0:
                    raise HD2SchemaError("Invalid planet region identifier")
                if (planet, region) in result:
                    raise HD2SchemaError("Duplicate planet region identifier")
                result[planet, region] = row
            return result

        static = indexed_regions(static_rows)
        regions = []
        for (planet_index, region_index), row in indexed_regions(rows).items():
            detail = static.get((planet_index, region_index), {})
            available = row.get("isAvailable")
            regions.append(PlanetRegion(
                planet_index=planet_index, index=region_index,
                faction=Faction.parse(row.get("owner")), health=number(row.get("health")),
                max_health=number(detail.get("maxHealth")), players=integer(row.get("players")),
                available=available if isinstance(available, bool) else None,
                # The public API spells this field "regerPerSecond".
                regen_rate=number(row.get("regerPerSecond")),
                damage_multiplier=number(detail.get("damageMultiplier")),
                region_size=integer(detail.get("regionSize")),
            ))
        return regions

    async def get_special_units(self) -> list[SpecialUnit]:
        status, definitions = await gather_cancel_on_error(
            self._status(), self._resource("effects", self.settings.static_ttl),
        )
        effects = _indexed(require_list(definitions.payload), "id")
        rows = require_list(require_object(
            status.payload, ("planetActiveEffects",),
        )["planetActiveEffects"])
        # Group public markers; neither marker counts nor effect values describe
        # enemy strength, so no troop counts or combat advantage are inferred.
        matches: dict[tuple[str, Faction], tuple[set[int], set[int]]] = {}
        for value in rows:
            row = require_object(value, ("index", "galacticEffectId"))
            planet_index, effect_id = integer(row["index"]), integer(row["galacticEffectId"])
            if planet_index is None or planet_index < 0 or effect_id is None or effect_id < 0:
                raise HD2SchemaError("Invalid active effect identifier")
            effect = effects.get(effect_id, {})
            value_types = require_list(effect.get("valueTypes", []))
            values = require_list(effect.get("values", []))
            resource = None
            if len(value_types) == len(values) and integer(effect.get("effectType")) == 40:
                resource = next((integer(v) for t, v in zip(value_types, values) if t == 16), None)
            known = next((item for item in SPECIAL_UNIT_CATALOG
                          if effect_id in item[3:] or (item[2] and item[2] == resource)), None)
            if known is None and integer(effect.get("effectType")) != 40:
                continue
            name, faction = known[:2] if known else (f"未知特殊部队 #{effect_id}", Faction.UNKNOWN)
            planets, identifiers = matches.setdefault((name, faction), (set(), set()))
            planets.add(planet_index)
            identifiers.add(effect_id)
        return [SpecialUnit(name, faction, tuple(sorted(planets)), tuple(sorted(identifiers)))
                for (name, faction), (planets, identifiers) in sorted(matches.items())]

    async def get_statistics(self) -> GlobalStatistics:
        response, planets = await gather_cancel_on_error(
            self._resource("statistics", self.settings.statistics_ttl), self.get_planets(),
        )
        galaxy = require_object(
            require_object(response.payload, ("galaxy_stats",))["galaxy_stats"],
        )
        if not any(key in galaxy for key in ("missionsWon", "missionsLost", "bugKills",
                                            "automatonKills", "illuminateKills", "deaths")):
            raise HD2SchemaError("Galaxy statistics fields missing")
        groups: dict[Faction, list[int | None]] = {faction: [] for faction in Faction}
        for planet in planets:
            faction = planet.faction
            if planet.event is not None and planet.event.event_type == 1:
                faction = planet.event.faction
            groups[faction].append(planet.players)
        return GlobalStatistics(
            players=sum_known(planet.players for planet in planets),
            faction_players={faction: sum_known(values) for faction, values in groups.items()},
            missions_won=integer(galaxy.get("missionsWon")),
            missions_lost=integer(galaxy.get("missionsLost")),
            terminid_kills=integer(galaxy.get("bugKills")),
            automaton_kills=integer(galaxy.get("automatonKills")),
            illuminate_kills=integer(galaxy.get("illuminateKills")),
            deaths=integer(galaxy.get("deaths")),
        )
