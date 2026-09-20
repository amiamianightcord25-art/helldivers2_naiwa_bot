"""Typed adapter for the public helldivers2.dev v1 API."""

import logging

from hd2bot.hd2.base import HD2Provider
from hd2bot.hd2.errors import HD2SchemaError
from hd2bot.hd2.models import (
    Campaign,
    Dispatch,
    Faction,
    GlobalStatistics,
    MajorOrder,
    OrderTask,
    Planet,
    PlanetEvent,
    Reward,
    WarStatus,
)
from hd2bot.hd2.parsing import (
    date,
    integer,
    list_value,
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

logger = logging.getLogger(__name__)


def _faction(value) -> Faction:
    # The community API uses singular "Automaton" in its current schema.
    if isinstance(value, str) and value.casefold() == "automaton":
        return Faction.AUTOMATONS
    return Faction.parse(value)


def _count(value) -> int | None:
    parsed = integer(value)
    return parsed if parsed is not None and parsed >= 0 else None


def _amount(value) -> float | None:
    parsed = number(value)
    return parsed if parsed is not None and parsed >= 0 else None


def _localized(value) -> str | None:
    if isinstance(value, dict):
        return text(value.get("zh-Hans")) or text(value.get("en-US"))
    return text(value)


def _records(payload, parser, label: str) -> list:
    rows = require_list(payload)
    parsed = []
    for row in rows:
        try:
            parsed.append(parser(row))
        except HD2SchemaError:
            # Never log API bodies: future upstream fields could contain secrets.
            logger.warning("Skipped invalid community %s record", label)
    if rows and not parsed:
        raise HD2SchemaError(f"No valid community {label} records")
    return parsed


def _dispatch(payload) -> Dispatch:
    row = require_object(payload, ("id", "message"))
    dispatch_id = _count(row["id"])
    message = _localized(row["message"])
    if dispatch_id is None or message is None:
        raise HD2SchemaError("Dispatch identity or message is invalid")
    # v2 provides ISO timestamps; do not reinterpret relative numeric seconds
    # as Unix time if an upstream response changes its shape.
    published = row.get("published")
    published_at = date(published) if isinstance(published, str) else None
    return Dispatch(dispatch_id, message, published_at)


def _event(payload) -> PlanetEvent | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        logger.warning("Ignored invalid community planet event")
        return None
    health, maximum = _amount(payload.get("health")), _amount(payload.get("maxHealth"))
    return PlanetEvent(
        id=_count(payload.get("id")), event_type=integer(payload.get("eventType")),
        faction=_faction(payload.get("faction")), health=health, max_health=maximum,
        starts_at=date(payload.get("startTime")), ends_at=date(payload.get("endTime")),
        progress=progress(health, maximum),
    )


def _planet(payload) -> Planet:
    row = require_object(payload, ("index", "currentOwner"))
    index = _count(row["index"])
    if index is None or not isinstance(row["currentOwner"], (str, int)):
        raise HD2SchemaError("Planet identity or ownership is invalid")
    if isinstance(row["currentOwner"], bool):
        raise HD2SchemaError("Planet ownership is invalid")
    metadata = metadata_for(index)
    source_name = _localized(row.get("name"))
    name = text(metadata.get("name")) or source_name or f"星球 #{index}"
    english_name = text(metadata.get("english_name"))
    aliases = tuple(dict.fromkeys(
        alias for alias in (*metadata.get("aliases", ()), english_name, source_name)
        if text(alias)
    ))
    owner = _faction(row["currentOwner"])
    health, maximum = _amount(row.get("health")), _amount(row.get("maxHealth"))
    position = object_value(row.get("position"))
    x, y = number(position.get("x")), number(position.get("y"))
    source_hazards = tuple(
        hazard for item in list_value(row.get("hazards"))
        if (hazard := text(object_value(item).get("name"))) is not None
        and hazard.casefold() != "none"
    )
    return Planet(
        index=index, name=name, english_name=english_name, aliases=aliases,
        sector=text(metadata.get("sector")) or text(row.get("sector")), faction=owner,
        health=health, max_health=maximum,
        liberation=100.0 if owner == Faction.HUMANS else progress(health, maximum),
        players=_count(object_value(row.get("statistics")).get("playerCount")),
        regen_rate=_amount(row.get("regenPerSecond")), event=_event(row.get("event")),
        biome=text(metadata.get("biome")) or text(object_value(row.get("biome")).get("name")),
        hazards=tuple(metadata.get("hazards") or source_hazards),
        position=(x, y) if x is not None and y is not None else None,
        waypoints=tuple(
            index for item in list_value(row.get("waypoints"))
            if (index := _count(item)) is not None
        ),
        disabled=row.get("disabled") is True,
    )


def _campaign(payload) -> Campaign:
    row = require_object(payload, ("id", "planet"))
    campaign_id = _count(row["id"])
    if campaign_id is None:
        raise HD2SchemaError("Campaign id is invalid")
    planet = _planet(row["planet"])
    return Campaign(
        id=campaign_id, planet=planet, type=integer(row.get("type")),
        # Keep the upstream campaign field; combat grouping uses the planet owner
        # or event attacker in the service, not this frequently-Humans value.
        count=_count(row.get("count")), faction=_faction(row.get("faction")),
    )


def _task(payload, task_progress=None) -> OrderTask:
    row = require_object(payload, ("type",))
    task_type = integer(row["type"])
    if task_type is None:
        raise HD2SchemaError("Order task type is invalid")
    raw_values = require_list(row.get("values", []))
    raw_types = require_list(row.get("valueTypes", []))
    values = tuple(number(value) for value in raw_values)
    value_types = tuple(integer(value) for value in raw_types)
    if any(value is None for value in (*values, *value_types)):
        raise HD2SchemaError("Order task values are invalid")
    if len(values) != len(value_types):
        raise HD2SchemaError("Order task value vectors have different lengths")
    parameters = dict(zip(value_types, values))
    return OrderTask(
        type=task_type, values=values, value_types=value_types,
        progress=_amount(task_progress),
        # Type 11 is the observed planetary liberation objective. Unknown task
        # types retain raw values; their target semantics must not be guessed.
        target=_amount(parameters.get(3)) if task_type == 11 else None,
        planet_index=_count(parameters.get(12)) if task_type == 11 else None,
        description=_localized(row.get("description")),
    )


def _order(payload) -> MajorOrder:
    row = require_object(payload, ("id", "tasks"))
    order_id = _count(row["id"])
    if order_id is None:
        raise HD2SchemaError("Major order id is invalid")
    task_rows = require_list(row["tasks"])
    progresses = list_value(row.get("progress"))
    tasks = []
    for index, task in enumerate(task_rows):
        tasks.append(_task(task, progresses[index] if index < len(progresses) else None))
    raw_rewards = list_value(row.get("rewards"))
    if not raw_rewards and isinstance(row.get("reward"), dict):
        raw_rewards = [row["reward"]]
    rewards = [
        Reward(type=integer(item.get("type")), amount=_count(item.get("amount")))
        for item in raw_rewards if isinstance(item, dict)
    ]
    return MajorOrder(
        id=order_id, title=_localized(row.get("title")),
        description=_localized(row.get("description")), briefing=_localized(row.get("briefing")),
        tasks=tasks, rewards=rewards, expires_at=date(row.get("expiration")),
    )


class CommunityProvider(HD2Provider):
    name = "community"

    def __init__(self, http, settings):
        self.http = http
        self.settings = settings

    async def _war_payload(self) -> dict:
        row = require_object(
            await self.http.get("/api/v1/war", ttl=self.settings.cache_ttl),
            ("started", "statistics"),
        )
        if date(row["started"]) is None or not isinstance(row["statistics"], dict):
            raise HD2SchemaError("Community war response is invalid")
        return row

    async def get_war(self) -> WarStatus:
        row = await self._war_payload()
        return WarStatus(
            started_at=date(row.get("started")), ended_at=date(row.get("ended")),
            players=_count(row["statistics"].get("playerCount")),
            impact_multiplier=_amount(row.get("impactMultiplier")),
            # The v1 `now` value incorrectly encodes war-relative seconds as a
            # 1972 timestamp. It is neither wall time nor a trustworthy war clock.
        )

    async def get_planets(self) -> list[Planet]:
        payload = await self.http.get("/api/v1/planets", ttl=self.settings.cache_ttl)
        return _records(payload, _planet, "planet")

    async def get_campaigns(self) -> list[Campaign]:
        payload = await self.http.get("/api/v1/campaigns", ttl=self.settings.cache_ttl)
        return _records(payload, _campaign, "campaign")

    async def get_major_order(self) -> list[MajorOrder]:
        payload = await self.http.get("/api/v1/assignments", ttl=self.settings.order_ttl)
        return _records(payload, _order, "major order")

    async def get_dispatches(self) -> list[Dispatch]:
        payload = await self.http.get("/api/v2/dispatches", ttl=self.settings.order_ttl)
        return sorted(_records(payload, _dispatch, "dispatch"), key=lambda item: item.id,
                      reverse=True)

    async def get_statistics(self) -> GlobalStatistics:
        war, planets = await gather_cancel_on_error(self._war_payload(), self.get_planets())
        stats = war["statistics"]
        groups: dict[Faction, list[int | None]] = {faction: [] for faction in Faction}
        for planet in planets:
            faction = (planet.event.faction
                       if planet.event is not None and planet.event.event_type == 1
                       else planet.owner)
            groups[faction].append(planet.players)
        return GlobalStatistics(
            players=_count(stats.get("playerCount")),
            faction_players={faction: sum_known(values) for faction, values in groups.items()},
            missions_won=_count(stats.get("missionsWon")),
            missions_lost=_count(stats.get("missionsLost")),
            terminid_kills=_count(stats.get("terminidKills")),
            automaton_kills=_count(stats.get("automatonKills")),
            illuminate_kills=_count(stats.get("illuminateKills")), deaths=_count(stats.get("deaths")),
        )
