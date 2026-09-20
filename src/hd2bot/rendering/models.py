"""Presentation-only values shared by the domain formatter and HTML renderer."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CardMetric:
    label: str
    value: str
    hint: str = ""


@dataclass(frozen=True)
class CardRow:
    label: str
    value: str
    detail: str = ""
    progress: float | None = None


@dataclass(frozen=True)
class CardSection:
    title: str
    rows: tuple[CardRow, ...]
    full_width: bool = False


@dataclass(frozen=True)
class MapPlanet:
    """A planet projected into the map's 1000 × 1000 drawing area."""

    index: int
    name: str
    x: float
    y: float
    faction: str
    active: bool = False
    defense: bool = False
    major: bool = False
    disabled: bool = False
    focused: bool = False
    marker: str = ""
    marker_dx: float = 20
    marker_dy: float = -39


@dataclass(frozen=True)
class MapLink:
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass(frozen=True)
class StarMap:
    planets: tuple[MapPlanet, ...]
    links: tuple[MapLink, ...]
    focus_name: str = ""


@dataclass(frozen=True)
class CardTile:
    number: int
    name: str
    detail: str = ""
    image_data: str = ""


@dataclass(frozen=True)
class VoteDot:
    x: float
    y: float
    color: str
    radius: float


@dataclass(frozen=True)
class VoteLegend:
    planet_id: int
    name: str
    votes: int
    percentage: str
    color: str
    dots: int


@dataclass(frozen=True)
class VoteChart:
    title: str
    total: int
    dots: tuple[VoteDot, ...] = ()
    legend: tuple[VoteLegend, ...] = ()
    subtitle: str = ""
    empty_message: str = ""
    note: str = "点阵按票数比例缩放；准确票数与占比见下方。"


@dataclass(frozen=True)
class QueryCard:
    title: str
    subtitle: str = ""
    eyebrow: str = "HELLDIVERS 2"
    metrics: tuple[CardMetric, ...] = ()
    sections: tuple[CardSection, ...] = ()
    notices: tuple[str, ...] = ()
    footer: tuple[str, ...] = ()
    star_map: StarMap | None = None
    image_data: str = ""
    image_caption: str = ""
    columns: int = 3
    gallery: tuple[CardTile, ...] = ()
    vote_charts: tuple[VoteChart, ...] = ()
