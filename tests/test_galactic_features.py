"""Public feature contracts, war-clock anchors, and genuine empty/unknown states."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from hd2bot.galactic_features import (
    episode_status,
    format_episodes,
    format_global_events,
    format_planet_regions,
    format_space_stations,
    format_special_units,
    tactical_status,
)
from hd2bot.hd2.errors import HD2SchemaError, HD2UnavailableError
from hd2bot.hd2.models import Episode, EpisodePhase, Faction, GlobalEvent
from hd2bot.hd2.providers import captured
from hd2bot.hd2.providers.captured import CapturedAPIProvider
from hd2bot.hd2.providers.fallback import FallbackProvider
from hd2bot.hd2.providers.mock import MockProvider
from hd2bot.hd2.service import HD2Service

METHODS = ("get_space_stations", "get_global_events", "get_episodes",
           "get_planet_regions", "get_special_units")
ANCHOR = datetime(2026, 9, 16, 8, tzinfo=UTC)


class PublicHTTP:
    def __init__(self):
        self.calls = []
        self.data = {
            "/api/WarSeason/current/WarID": {"id": 99},
            "/api/WarSeason/99/Status": {
                "time": 1000, "planetStatus": [],
                "spaceStations": [{"id32": 749875195, "planetIndex": 127}],
                "globalEvents": [{"eventId": 700, "title": "事件标题", "message": "<i=1>完整正文</i>",
                                  "expireTime": 2000, "race": 4, "planetIndices": [127],
                                  "effectIds": [300], "assignmentId32": 123}],
                "planetRegions": [{"planetIndex": 127, "regionIndex": 2, "owner": 3,
                                   "health": 100, "players": 0, "isAvailable": False,
                                   "regerPerSecond": 1.2}],
                "planetActiveEffects": [{"index": 127, "galacticEffectId": 1202},
                                        {"index": 127, "galacticEffectId": 1203},
                                        {"index": 64, "galacticEffectId": 8000}],
            },
            "/api/WarSeason/99/WarInfo": {
                "startDate": 1000000, "planetInfos": [],
                "planetRegions": [{"planetIndex": 127, "regionIndex": 2, "maxHealth": 400,
                                   "damageMultiplier": 1.5, "regionSize": 2},
                                  {"planetIndex": 64, "regionIndex": 5, "maxHealth": 500}],
            },
            "/api/SpaceStation/99/749875195": {
                "id32": 749875195, "planetIndex": 127, "flags": 1,
                "currentElectionId": "election-public",
                "currentElectionEndWarTime": 1500,
                "tacticalActions": [{
                    "id32": 30, "name": "测试行动", "status": 1,
                    "statusExpireAtWarTimeSeconds": 1600,
                    "strategicDescription": "<i=1>实际公开效果</i>",
                    "cost": [{"itemMixId": 3992382197, "currentValue": 0, "targetValue": 500,
                              "maxDonationAmount": 75, "maxDonationPeriodSeconds": 86400}],
                }],
                "votes": {"options": [{"metaId": 64, "count": 999}]},
            },
            "/api/ElectionV2/99/election-public": {
                "context": 3953768710, "status": 1,
                "options": [{"id": "option-64", "text": "示例星球64",
                              "metaId": 64, "count": 999}],
            },
            "/api/Episode/99": {"episodes": [{
                "id32": 21, "title": "TEST EPISODE", "description": "战役完整描述",
                "race": 2, "status": 0, "startWarTime": 500,
                "phases": [{"id32": 31, "status": 2, "introTitle": "过去阶段",
                            "introMessage": "过去任务全文", "outroTitle": "已成功",
                            "outroMessage": "阶段结果全文", "rewards": []},
                           {"id32": 32, "status": 0, "introTitle": "当前阶段",
                            "introMessage": "进行中的目标全文", "rewards": [
                                {"mixId": 897894480, "amount": 45}]}],
                "rewards": [{"mixId": 555, "amount": 1}],
            }]},
            "/api/WarSeason/GalacticWarEffects": [
                {"id": 1202, "effectType": 40, "values": [2922304745, 0], "valueTypes": [16, 0]},
                {"id": 1203, "effectType": 39, "values": [1], "valueTypes": [1]},
                {"id": 8000, "effectType": 40, "values": [999999], "valueTypes": [16]},
            ],
        }

    async def get(self, path, ttl=0):
        self.calls.append(path)
        await asyncio.sleep(0)
        value = self.data[path]
        if isinstance(value, Exception):
            raise value
        return deepcopy(value)


@pytest.fixture
def public(monkeypatch):
    monkeypatch.setattr(captured, "utcnow", lambda: ANCHOR)
    settings = SimpleNamespace(cache_ttl=30, static_ttl=3600, order_ttl=60,
                               statistics_ttl=60, stale_ttl=900)
    http = PublicHTTP()
    provider = CapturedAPIProvider(http, settings)
    service = HD2Service(FallbackProvider([provider]), settings)
    return http, provider, service


async def test_all_five_methods_reuse_status_and_keep_source_sample(public):
    http, _, service = public
    results = await asyncio.gather(*(getattr(service, method)() for method in METHODS))
    assert all(result.value for result in results)
    assert all(result.source == "captured" and result.fetched_at == ANCHOR for result in results)
    assert http.calls.count("/api/WarSeason/99/Status") == 1
    assert http.calls.count("/api/WarSeason/current/WarID") == 1


async def test_dates_use_status_clock_not_war_info_start_and_stay_stable(public, monkeypatch):
    _, provider, _ = public
    station = (await provider.get_space_stations())[0]
    event = (await provider.get_global_events())[0]
    episode = (await provider.get_episodes())[0]
    assert station.election_ends_at == ANCHOR + timedelta(seconds=500)
    assert station.tactical_actions[0].expires_at == ANCHOR + timedelta(seconds=600)
    assert event.expires_at == ANCHOR + timedelta(seconds=1000)
    assert episode.starts_at == ANCHOR - timedelta(seconds=500)
    assert episode.ends_at is None
    monkeypatch.setattr(captured, "utcnow", lambda: ANCHOR + timedelta(hours=3))
    assert (await provider.get_space_stations())[0].election_ends_at == station.election_ends_at
    assert (await provider.get_episodes())[0].starts_at == episode.starts_at


async def test_dss_zero_donation_and_public_votes_are_rendered(public):
    _, provider, _ = public
    stations = await provider.get_space_stations()
    body = format_space_stations(stations)
    assert stations[0].tactical_actions[0].costs[0].current == 0
    assert "0.00%" in body and "普通样本" in body and "准备中" in body
    assert "实际公开效果" in body and "<i=" not in body
    assert "999" in body and "票数来自官方公开 ElectionV2" in body


async def test_dss_election_uses_current_id_and_parses_options(public):
    http, provider, _ = public
    stations = await provider.get_space_stations()
    election = stations[0].election
    assert election is not None and election.id == "election-public"
    assert election.options[0].meta_id == 64 and election.options[0].count == 999
    assert "/api/ElectionV2/99/election-public" in http.calls


async def test_unknown_dss_values_never_become_zero_or_active(public):
    http, provider, _ = public
    action = http.data["/api/SpaceStation/99/749875195"]["tacticalActions"][0]
    action["status"] = 777
    action["statusExpireAtWarTimeSeconds"] = 0
    action["cost"][0].pop("currentValue")
    station = (await provider.get_space_stations())[0]
    assert station.tactical_actions[0].expires_at is None
    assert station.tactical_actions[0].costs[0].current is None
    assert "未知状态（777）" in format_space_stations([station])


async def test_region_join_keeps_unavailable_and_zero_players_and_excludes_static_only(public):
    _, provider, _ = public
    regions = await provider.get_planet_regions()
    assert len(regions) == 1
    region = regions[0]
    assert region.max_health == 400 and region.health == 100
    assert region.available is False and region.players == 0
    assert region.regen_rate == 1.2
    body = format_planet_regions(regions, 127)
    assert "75.00%" in body and "不可进入" in body and "在线人数：0" in body
    assert "没有对应" in format_planet_regions(regions, 64)


async def test_special_unit_markers_deduplicate_and_unknown_type40_stays_unknown(public):
    _, provider, _ = public
    units = await provider.get_special_units()
    known = next(unit for unit in units if unit.name == "JET BRIGADE")
    unknown = next(unit for unit in units if unit.faction == Faction.UNKNOWN)
    assert known.planet_indices == (127,) and known.effect_ids == (1202, 1203)
    assert unknown.planet_indices == (64,) and unknown.name == "未知特殊部队 #8000"
    assert "JET BRIGADE" in format_special_units(units)


async def test_episodes_default_objective_and_explicit_full_history(public):
    _, provider, _ = public
    episodes = await provider.get_episodes()
    overview = format_episodes(episodes)
    details = format_episodes(episodes, 21)
    assert "进行中的目标全文" in overview
    assert "过去任务全文" not in overview
    assert "过去任务全文" in details and "阶段结果全文" in details
    assert "奖章 × 45" in details and "物品 #555" in details
    assert "未找到" in format_episodes(episodes, 500)


async def test_global_event_preserves_full_body_and_identifiers(public):
    _, provider, _ = public
    events = await provider.get_global_events()
    event = events[0]
    assert event.faction == Faction.ILLUMINATE
    assert event.effect_ids == (300,) and event.assignment_id == 123
    body = format_global_events(events, 700)
    assert "完整正文" in body and "事件标题" in body and "#123" in body
    assert "未找到" in format_global_events(events, 123)
    long_text = "完整内容" * 2000
    assert long_text in format_global_events([GlobalEvent(1, message=long_text)])


@pytest.mark.parametrize(("field", "method"), [
    ("spaceStations", "get_space_stations"), ("globalEvents", "get_global_events"),
    ("planetRegions", "get_planet_regions"), ("planetActiveEffects", "get_special_units"),
])
async def test_missing_resource_is_not_reported_as_empty(public, field, method):
    http, provider, _ = public
    del http.data["/api/WarSeason/99/Status"][field]
    with pytest.raises(HD2SchemaError):
        await getattr(provider, method)()


@pytest.mark.parametrize(("field", "method"), [
    ("spaceStations", "get_space_stations"), ("globalEvents", "get_global_events"),
    ("planetRegions", "get_planet_regions"), ("planetActiveEffects", "get_special_units"),
])
async def test_public_empty_resources_are_valid(public, field, method):
    http, provider, _ = public
    http.data["/api/WarSeason/99/Status"][field] = []
    assert await getattr(provider, method)() == []


async def test_station_error_does_not_generate_a_phantom_empty_resource(public):
    http, provider, _ = public
    http.data["/api/SpaceStation/99/749875195"] = HD2UnavailableError("public source unavailable")
    with pytest.raises(HD2UnavailableError):
        await provider.get_space_stations()


@pytest.mark.parametrize("war_time", [None, "1000", -1, True])
async def test_missing_invalid_war_clock_leaves_all_dates_unknown(public, war_time):
    http, provider, _ = public
    http.data["/api/WarSeason/99/Status"]["time"] = war_time
    assert (await provider.get_global_events())[0].expires_at is None
    assert (await provider.get_episodes())[0].starts_at is None
    assert (await provider.get_space_stations())[0].election_ends_at is None


@pytest.mark.parametrize("method", METHODS)
async def test_mock_features_work_fully_offline(method):
    assert await getattr(MockProvider(), method)()


def test_unknown_status_and_empty_states_are_explained():
    assert tactical_status(None) == "暂无数据"
    assert episode_status(999) == "未知状态（999）"
    assert "没有公开战役" in format_episodes([])
    assert "没有空间站" in format_space_stations([])
    assert "没有公开全球事件" in format_global_events([])
    assert "没有识别到" in format_special_units([])
    assert "未知状态（99）" in format_episodes([Episode(1, title="未知测试", status=99)])


def test_english_episode_titles_are_preserved_and_repeated_intro_is_not_duplicated():
    episode = Episode(1, title="CONTAINMENT", status=0, description="同一段背景",
                      intro_message="同一段背景",
                      phases=[EpisodePhase(2, intro_title="DEPLOYMENT", status=0)])
    body = format_episodes([episode], 1)
    assert "CONTAINMENT" in body and "DEPLOYMENT" in body
    assert body.count("同一段背景") == 1
