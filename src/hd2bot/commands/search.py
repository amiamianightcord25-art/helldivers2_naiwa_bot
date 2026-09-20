"""Match known names exactly before offering bounded, nonbinding suggestions."""

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from hd2bot.hd2.errors import CommandError
from hd2bot.hd2.models import Planet
from hd2bot.services.localization import metadata_for, normalize


@dataclass
class SearchResult:
    match: Planet | None = None
    candidates: list[Planet] = field(default_factory=list)


def _names(values) -> set[str]:
    return {key for value in values if isinstance(value, str) and (key := normalize(value))}


def _own_names(planet: Planet) -> set[str]:
    return _names((planet.name, planet.english_name, *planet.aliases))


def _all_names(planet: Planet, own_names: set[str]) -> set[str]:
    metadata = metadata_for(planet.index)
    english = metadata.get("english_name")
    # Synthetic providers may reuse an index for a different demonstration world.
    # Do not attach an unrelated real planet's aliases to it.
    if planet.english_name and english and normalize(planet.english_name) != normalize(english):
        return own_names
    return own_names | _names((
        metadata.get("name"), english, *metadata.get("aliases", ()),
    ))


def _result(planets: list[Planet]) -> SearchResult:
    if len(planets) == 1:
        return SearchResult(match=planets[0])
    return SearchResult(candidates=planets[:5])


def search_planets(query: str, planets: list[Planet]) -> SearchResult:
    if not isinstance(query, str) or not (key := normalize(query)):
        raise CommandError("请输入星球名称或编号，例如：星球 Meridia。")
    # Deduplicate by the API's stable identity, preserving the caller's objects.
    unique = list({planet.index: planet for planet in reversed(planets)}.values())
    unique.reverse()
    if re.fullmatch(r"#?[0-9]+", key):
        digits = key.removeprefix("#").lstrip("0") or "0"
        return _result([planet for planet in unique if str(planet.index) == digits])

    names = [(planet, _own_names(planet)) for planet in unique]
    direct = [planet for planet, aliases in names if key in aliases]
    if direct:
        return _result(direct)

    names = [(planet, _all_names(planet, aliases)) for planet, aliases in names]
    exact = [planet for planet, aliases in names if key in aliases]
    if exact:
        return _result(exact)

    partial = [
        (planet, max(len(key) / len(alias) for alias in aliases if key in alias))
        for planet, aliases in names if any(key in alias for alias in aliases)
    ]
    if partial:
        partial.sort(key=lambda item: (-item[1], item[0].index))
        return _result([planet for planet, score in partial])

    cutoff = 0.75 if len(key) <= 2 else 0.6
    suggested = []
    for planet, aliases in names:
        score = max((SequenceMatcher(None, key, alias).ratio() for alias in aliases), default=0)
        if score >= cutoff:
            suggested.append((planet, score))
    suggested.sort(key=lambda item: (-item[1], item[0].index))
    # A spelling approximation always requires user selection, even if only one
    # candidate remains. It must not silently execute a query for a wrong world.
    return SearchResult(candidates=[planet for planet, score in suggested[:5]])
