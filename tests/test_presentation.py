from datetime import UTC, datetime

import pytest

from hd2bot.application import create_career_service, create_service
from hd2bot.career.models import FIELDS, CareerStats
from hd2bot.config import Settings
from hd2bot.hd2.models import Campaign, Faction, Planet
from hd2bot.hd2.service import DataResult
from hd2bot.presentation import campaign_card, career_card
from hd2bot.router import CommandRouter


@pytest.mark.parametrize("command", ["帮助", "未知命令", "星球", "星球 找不到的名字xyz", "玩家",
                                     "防守", "进攻", "战绩 123"])
async def test_short_replies_and_errors_stay_text(command):
    settings = Settings(provider="mock")
    async with create_service(settings) as service, create_career_service(settings) as career:
        router = CommandRouter(service, career)
        reply = await router.respond(command)
        assert reply.card is None
        assert reply.text == await router.handle(command)


@pytest.mark.parametrize("command", ["战况", "星球 Meridia", "战绩", "查战绩"])
async def test_detail_cards_preserve_a_full_cli_text_alternative(command):
    settings = Settings(provider="mock")
    async with create_service(settings) as service, create_career_service(settings) as career:
        reply = await CommandRouter(service, career).respond(command)
        assert reply.card is not None and len(reply.text) > 100
        assert any("模拟" in notice for notice in reply.card.notices)
        assert reply.card.footer


def test_career_card_keeps_all_27_values_and_true_cached_timestamp():
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    stats = CareerStats({key: index for index, key in enumerate(FIELDS)}, stamp,
                        cached=True, local_cached=True)
    card = career_card(stats)
    rows = [row for section in card.sections for row in section.rows]
    assert len(rows) == 27
    assert sorted(int(row.value.replace(",", "")) for row in rows) == list(range(27))
    assert "2026-01-01" in " ".join(card.footer)
    assert "服务端缓存" in " ".join(card.footer) and "机器人本地缓存" in " ".join(card.footer)
    assert "单位待核实" in " ".join(row.label for row in rows)


def test_many_campaigns_are_not_truncated_and_stale_is_visible():
    planets = [Planet(i, f"Planet {i}", faction=Faction.AUTOMATONS, players=i,
                      liberation=float(i)) for i in range(33)]
    campaigns = [Campaign(i, p) for i, p in enumerate(planets)]
    results = [DataResult(campaigns, "captured", datetime(2026, 1, 1, tzinfo=UTC), stale=True)]
    card = campaign_card(campaigns, None, results)
    assert sum(len(section.rows) for section in card.sections) == 33
    assert any("最近缓存" in notice for notice in card.notices)
    assert campaign_card(campaigns[:4], None, results) is None
