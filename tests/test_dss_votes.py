import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

from hd2bot.galactic_features import format_dss_votes, format_space_stations
from hd2bot.hd2.models import DSSElection, DSSVoteOption, SpaceStation
from hd2bot.hd2.providers.captured import CapturedAPIProvider
from hd2bot.hd2.providers.fallback import FallbackProvider
from hd2bot.hd2.service import DataResult, HD2Service
from hd2bot.presentation import ChatContext
from hd2bot.router import CommandRouter
from hd2bot.services.notifications import NotificationManager
from hd2bot.storage.database import Database


class ElectionHTTP:
    def __init__(self):
        self.calls = []
        self.data = {
            "/api/WarSeason/current/WarID": {"id": 801},
            "/api/WarSeason/801/Status": {
                "time": 1000,
                "planetStatus": [],
                "spaceStations": [{"id32": 749875195, "planetIndex": 158}],
            },
            "/api/SpaceStation/801/749875195": {
                "id32": 749875195,
                "planetIndex": 158,
                "flags": 1,
                "currentElectionEndWarTime": 2000,
                "currentElectionId": "election-one",
                "tacticalActions": [],
                # This legacy field must not be treated as vote data.
                "votes": {"options": [{"metaId": 158, "count": 9999}]},
            },
            "/api/ElectionV2/801/election-one": {
                "context": 3953768710,
                "status": 1,
                "options": [
                    {"id": "option-a", "text": "科埃佩萨IV", "metaId": 158, "count": 3942},
                    {"id": "option-b", "text": "费里", "metaId": 261, "count": 525},
                ],
            },
        }

    async def get(self, path, ttl=0):
        self.calls.append(path)
        await asyncio.sleep(0)
        return deepcopy(self.data[path])


def _settings():
    return SimpleNamespace(cache_ttl=30, static_ttl=3600, order_ttl=60,
                           statistics_ttl=60, stale_ttl=900)


async def test_public_election_id_is_followed_and_vote_counts_are_parsed():
    http = ElectionHTTP()
    provider = CapturedAPIProvider(http, _settings())

    stations = await provider.get_space_stations()
    station = stations[0]
    assert station.election_id == "election-one"
    assert station.election.options[0].count == 3942
    assert "/api/ElectionV2/801/election-one" in http.calls
    assert "9999" not in format_space_stations(stations)

    elections = await provider.get_dss_votes()
    assert elections[0].id == "election-one"
    assert "3,942" in format_dss_votes(elections)


async def test_dss_votes_command_uses_public_election_resource():
    http = ElectionHTTP()
    settings = _settings()
    service = HD2Service(FallbackProvider([CapturedAPIProvider(http, settings)]), settings)
    reply = await CommandRouter(service).respond("DSS票数", context=ChatContext("c2c", "x"))
    assert "科埃佩萨IV" in reply.text
    assert "3,942" in reply.text
    assert reply.card is not None
    assert reply.card.vote_charts[0].total == 4467


class VoteNotificationService:
    def __init__(self):
        self.now = datetime(2026, 9, 19, 8, tzinfo=UTC).timestamp()
        self.station = SpaceStation(
            749875195,
            158,
            1,
            election=DSSElection(
                "election-one", status=1,
                options=[DSSVoteOption(158, 12, "科埃佩萨IV"),
                         DSSVoteOption(261, 9, "费里")],
            ),
        )

    def clock(self):
        return self.now

    async def get_space_stations(self):
        return DataResult([self.station], "captured", datetime.fromtimestamp(self.now, UTC))


async def test_dss_subscription_notifies_when_public_vote_count_changes(tmp_path):
    service = VoteNotificationService()
    async with Database(tmp_path / "bot.db") as db:
        manager = NotificationManager(db, service, clock=service.clock)
        assert "已订阅" in await manager.handle_command("订阅", "DSS", "c2c", "user")
        service.station.election.options[0] = DSSVoteOption(158, 18, "科埃佩萨IV")
        sender_calls = []

        async def sender(scope, target, text):
            sender_calls.append((scope, target, text))

        await manager.tick(sender)
        assert len(sender_calls) == 1
        assert "迁移投票" in sender_calls[0][2]
        assert "12 票 → 18 票" in sender_calls[0][2]


async def test_dss_subscription_ignores_small_vote_counter_noise(tmp_path):
    service = VoteNotificationService()
    async with Database(tmp_path / "bot.db") as db:
        manager = NotificationManager(db, service, clock=service.clock)
        await manager.handle_command("订阅", "DSS", "c2c", "user")
        service.station.election.options = [DSSVoteOption(158, 121, "科埃佩萨IV"),
                                            DSSVoteOption(261, 91, "费里")]
        calls = []
        async def sender(*args):
            calls.append(args)
        await manager.tick(sender)
        assert calls == []
