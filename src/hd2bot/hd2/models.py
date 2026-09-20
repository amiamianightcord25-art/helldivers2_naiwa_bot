from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum


def utcnow() -> datetime:
    return datetime.now(UTC)


class Faction(str, Enum):
    HUMANS = "Humans"
    TERMINIDS = "Terminids"
    AUTOMATONS = "Automatons"
    ILLUMINATE = "Illuminate"
    UNKNOWN = "Unknown"

    @classmethod
    def parse(cls, value) -> "Faction":
        if isinstance(value, cls):
            return value
        numbers = {1: cls.HUMANS, 2: cls.TERMINIDS, 3: cls.AUTOMATONS, 4: cls.ILLUMINATE}
        if str(value).casefold() in {"automaton", "cyborgs"}:
            return cls.AUTOMATONS
        if isinstance(value, int) and not isinstance(value, bool):
            return numbers.get(value, cls.UNKNOWN)
        for faction in cls:
            if str(value).casefold() == faction.value.casefold():
                return faction
        return cls.UNKNOWN


@dataclass
class PlanetEvent:
    id: int | None = None
    event_type: int | None = None
    faction: Faction = Faction.UNKNOWN
    health: float | None = None
    max_health: float | None = None
    ends_at: datetime | None = None
    starts_at: datetime | None = None
    progress: float | None = None  # 0..100; enemy HP depleted


@dataclass
class Planet:
    index: int
    name: str
    english_name: str | None = None
    aliases: tuple[str, ...] = ()
    sector: str | None = None
    faction: Faction = Faction.UNKNOWN  # current owner; event has attacking faction
    health: float | None = None
    max_health: float | None = None
    liberation: float | None = None
    players: int | None = None
    regen_rate: float | None = None  # HP/second, not net player progress
    event: PlanetEvent | None = None
    biome: str | None = None
    hazards: tuple[str, ...] = ()
    position: tuple[float, float] | None = None
    waypoints: tuple[int, ...] = ()
    disabled: bool = False

    @property
    def owner(self) -> Faction:
        return self.faction


@dataclass
class Campaign:
    id: int
    planet: Planet
    type: int | None = None
    count: int | None = None
    faction: Faction = Faction.UNKNOWN
    strategic_priority: int = 0


@dataclass
class OrderTask:
    type: int | None = None
    values: tuple[int | float, ...] = ()
    value_types: tuple[int, ...] = ()
    progress: float | None = None
    target: float | None = None
    planet_index: int | None = None
    description: str | None = None


@dataclass
class Reward:
    type: int | None = None
    amount: int | None = None


@dataclass
class MajorOrder:
    id: int
    title: str | None = None
    description: str | None = None
    briefing: str | None = None
    tasks: list[OrderTask] = field(default_factory=list)
    rewards: list[Reward] = field(default_factory=list)
    expires_at: datetime | None = None
    status: str | None = None


@dataclass
class GlobalStatistics:
    players: int | None = None
    faction_players: dict[Faction, int | None] = field(default_factory=dict)
    missions_won: int | None = None
    missions_lost: int | None = None
    terminid_kills: int | None = None
    automaton_kills: int | None = None
    illuminate_kills: int | None = None
    deaths: int | None = None


@dataclass(frozen=True)
class Dispatch:
    id: int
    message: str
    published_at: datetime | None = None


@dataclass
class WarStatus:
    war_id: int | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    war_time: float | None = None
    players: int | None = None
    impact_multiplier: float | None = None
    events: tuple[str, ...] = ()
    status: str = "进行中"


@dataclass(frozen=True)
class GlobalEvent:
    id: int
    title: str | None = None
    message: str | None = None
    faction: Faction = Faction.UNKNOWN
    planet_indices: tuple[int, ...] = ()
    effect_ids: tuple[int, ...] = ()
    assignment_id: int | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class TacticalCost:
    item_id: int | None = None
    current: float | None = None
    target: float | None = None
    donation_limit: int | None = None
    donation_period_seconds: int | None = None


@dataclass(frozen=True)
class DSSVoteOption:
    """One public ElectionV2 destination and its current vote count."""

    meta_id: int
    count: int
    text: str | None = None
    id: str | None = None


@dataclass
class DSSElection:
    """Public DSS migration election returned by ElectionV2."""

    id: str
    context: int | None = None
    status: int | None = None
    options: list[DSSVoteOption] = field(default_factory=list)


@dataclass
class TacticalAction:
    id: int
    name: str | None = None
    status: int | None = None
    expires_at: datetime | None = None
    description: str | None = None
    strategic_description: str | None = None
    costs: list[TacticalCost] = field(default_factory=list)


@dataclass
class SpaceStation:
    id: int
    planet_index: int | None = None
    flags: int | None = None
    election_ends_at: datetime | None = None
    tactical_actions: list[TacticalAction] = field(default_factory=list)
    election_id: str | None = None
    election: DSSElection | None = None

    @property
    def current_election_id(self) -> str | None:
        """Alias matching the upstream JSON field name semantically."""
        return self.election_id


@dataclass(frozen=True)
class EpisodeReward:
    item_id: int | None = None
    amount: int | None = None


@dataclass
class EpisodePhase:
    id: int
    status: int | None = None
    intro_title: str | None = None
    intro_message: str | None = None
    outro_title: str | None = None
    outro_message: str | None = None
    rewards: list[EpisodeReward] = field(default_factory=list)


@dataclass
class Episode:
    id: int
    title: str | None = None
    description: str | None = None
    intro_message: str | None = None
    outro_message: str | None = None
    faction: Faction = Faction.UNKNOWN
    status: int | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    phases: list[EpisodePhase] = field(default_factory=list)
    rewards: list[EpisodeReward] = field(default_factory=list)


@dataclass(frozen=True)
class PlanetRegion:
    planet_index: int
    index: int
    faction: Faction = Faction.UNKNOWN
    health: float | None = None
    max_health: float | None = None
    players: int | None = None
    available: bool | None = None
    regen_rate: float | None = None
    damage_multiplier: float | None = None
    region_size: int | None = None


@dataclass(frozen=True)
class SpecialUnit:
    name: str
    faction: Faction
    planet_indices: tuple[int, ...] = ()
    effect_ids: tuple[int, ...] = ()


# Public identifier facts documented by Galactic-Wide-Web,
# utils/dataclasses/subfactions.py; names stay original where localization is unverified.
# Each row is (name, faction, resource hash, division effect, marker effect).
SPECIAL_UNIT_CATALOG = (
    ("JET BRIGADE", Faction.AUTOMATONS, 2922304745, 1202, 1203),
    ("PREDATOR STRAIN", Faction.TERMINIDS, 2313485354, 1243, 1245),
    ("SPORE BURST STRAIN", Faction.TERMINIDS, 2745424799, 1244, 1386),
    ("INCINERATION CORPS", Faction.AUTOMATONS, 1703232728, 1248, 1249),
    ("THE GREAT HOST", Faction.ILLUMINATE, 0, 1269, 1269),
    ("RUPTURE STRAIN", Faction.TERMINIDS, 2423391486, 1303, 1310),
    ("DRAGONROACHES", Faction.TERMINIDS, 2681574458, 1306, 1309),
    ("HIVE LORDS", Faction.TERMINIDS, 424440415, 1307, 1308),
    ("CYBORGS", Faction.AUTOMATONS, 141977090, 1360, 1361),
    ("MINDLESS MASSES", Faction.ILLUMINATE, 35348659, 1377, 1378),
    ("APPROPRIATORS", Faction.ILLUMINATE, 3792924074, 1380, 1379),
    ("INVASION FLEET", Faction.ILLUMINATE, 872028856, 1413, 1414),
    ("HEAVY SEAF PRESENCE", Faction.HUMANS, 3126357841, 1401, 1400),
    ("VOTE SNATCHERS", Faction.ILLUMINATE, 4253783814, 1402, 1403),
)
