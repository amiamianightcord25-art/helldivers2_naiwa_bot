"""Chinese presentation, game text cleanup and offline planet name aliases."""

import json
import re
import unicodedata
from functools import lru_cache
from importlib.resources import files

from hd2bot.hd2.models import Faction

FACTIONS = {
    Faction.HUMANS: "超级地球", Faction.TERMINIDS: "终结族",
    Faction.AUTOMATONS: "机器人", Faction.ILLUMINATE: "光能者", Faction.UNKNOWN: "未知阵营",
}
TERMS = {
    "Tremors": "地震", "Fire Tornadoes": "火焰龙卷风", "Intense Heat": "酷热",
    "Extreme Cold": "严寒", "Blizzards": "暴风雪", "Rainstorms": "暴雨",
    "Meteor Storms": "流星雨", "Acid Storms": "酸雨风暴", "Sandstorms": "沙尘暴",
    "Ion Storms": "离子风暴", "Thick Fog": "浓雾", "None": "无",
    "Super Earth": "超级地球", "Desert Cliffs": "沙漠峭壁",
}


@lru_cache(maxsize=1)
def _catalog() -> dict:
    return json.loads(files("hd2bot").joinpath("assets/planets.json").read_text(encoding="utf8"))


def metadata_for(index: int) -> dict:
    return _catalog().get(str(index), {})


def faction_name(faction: Faction) -> str:
    return FACTIONS.get(faction, "未知阵营")


def clean_text(value: str | None, fallback: str = "暂无数据") -> str:
    if not value:
        return fallback
    if re.fullmatch(r"(?:#[0-9a-fA-F]+|[A-Z][A-Z0-9_]{7,}|(?:loc|localization|game)[._:/].+)", value):
        return "文本尚未本地化"
    return re.sub(r"</?[a-zA-Z][^>]*>", "", value).strip() or fallback


def term(value: str | None) -> str:
    return TERMS.get(value, clean_text(value))


def normalize(value: str) -> str:
    return re.sub(r"[\s'’\-_.]+", "", unicodedata.normalize("NFKC", value).casefold())
