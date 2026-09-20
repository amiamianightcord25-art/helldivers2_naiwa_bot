"""Synthetic, reproducible development data. Never used as a live fallback."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

from hd2bot.hd2.base import HD2Provider
from hd2bot.hd2.models import (
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


class MockProvider(HD2Provider):
    name = "mock"

    def __init__(self, settings=None):
        self._started = utcnow()
        self._planets = [
            Planet(
                index=64, name="梅里迪亚", english_name="Meridia",
                aliases=("Meridia", "梅里迪亚", "梅里迪安"), sector="猎户座星区",
                faction=Faction.ILLUMINATE, health=600000, max_health=1000000,
                liberation=40.0, players=1234, regen_rate=2.5,
                biome="模拟黑洞", hazards=("模拟数据",), waypoints=(127,),
                position=(0.35, 0.25),
            ),
            Planet(
                index=127, name="天使进取", english_name="Angel's Venture",
                aliases=("Angel's Venture", "Angels Venture", "天使进取", "天使创业", "天使之愿"),
                sector="猎户座星区", faction=Faction.HUMANS,
                health=1000000, max_health=1000000, liberation=100.0, players=5678,
                position=(0.20, 0.15),
                event=PlanetEvent(
                    id=101, event_type=1, faction=Faction.TERMINIDS,
                    health=350000, max_health=1000000, progress=65.0,
                    starts_at=self._started - timedelta(hours=18),
                    ends_at=self._started + timedelta(hours=6),
                ),
            ),
            Planet(
                index=196, name="马勒维隆溪", english_name="Malevelon Creek",
                aliases=("Malevelon Creek", "马勒维隆溪", "溪地"), sector="塞弗林星区",
                faction=Faction.AUTOMATONS, health=250000, max_health=1000000,
                liberation=75.0, players=3000, regen_rate=5.0,
                position=(-0.45, 0.35),
            ),
            Planet(
                index=0, name="超级地球", english_name="Super Earth",
                aliases=("Super Earth", "超级地球"), sector="太阳星区",
                faction=Faction.HUMANS, health=1000000, max_health=1000000,
                liberation=100.0, players=88, waypoints=(64,),
                position=(0.0, 0.0),
            ),
        ]

    async def get_war(self) -> WarStatus:
        return WarStatus(
            war_id=801, started_at=datetime(2024, 2, 8, tzinfo=UTC),
            players=10000, impact_multiplier=0.02, events=("本地模拟演习",),
        )

    async def get_planets(self) -> list[Planet]:
        return deepcopy(self._planets)

    async def get_campaigns(self) -> list[Campaign]:
        return [
            Campaign(
                id=100 + planet.index, planet=planet,
                type=1 if planet.event else 0,
                faction=planet.event.faction if planet.event else planet.owner,
            )
            for planet in await self.get_planets() if planet.index != 0
        ]

    async def get_major_order(self) -> list[MajorOrder]:
        return [MajorOrder(
            id=1001, title="模拟主线：为了超级地球",
            description="完成梅里迪亚解放并守住天使进取。",
            briefing="这是离线开发演习，不代表当前真实战况。",
            tasks=[
                OrderTask(type=11, values=(1, 1, 64), value_types=(3, 11, 12),
                          progress=0, target=1, planet_index=64, description="解放梅里迪亚"),
                OrderTask(type=11, values=(1, 1, 127), value_types=(3, 11, 12),
                          progress=1, target=1, planet_index=127, description="守住天使进取"),
            ],
            rewards=[Reward(type=1, amount=45)],
            expires_at=self._started + timedelta(days=2), status="模拟",
        )]

    async def get_statistics(self) -> GlobalStatistics:
        return GlobalStatistics(
            players=10000,
            faction_players={
                Faction.TERMINIDS: 5678, Faction.AUTOMATONS: 3000,
                Faction.ILLUMINATE: 1234, Faction.HUMANS: 88,
            },
            missions_won=100000, missions_lost=2500,
            terminid_kills=5000000, automaton_kills=3000000,
            illuminate_kills=1000000, deaths=150000,
        )

    async def get_dispatches(self) -> list[Dispatch]:
        return [
            Dispatch(9003, "<i=3>模拟战报：防守演习</i>\n天使进取防守演习进行中。"
                     "此消息为离线测试数据。", self._started - timedelta(hours=1)),
            Dispatch(9002, "<i=3>模拟战报：补给线路</i>\n梅里迪亚与天使进取存在模拟连接。"
                     "连接不代表当前可部署。", self._started - timedelta(hours=3)),
            Dispatch(9001, "<i=3>模拟战报：演习开始</i>\n本地开发演习已启动。"
                     "本消息不代表真实银河事件。", self._started - timedelta(days=1)),
        ]

    async def get_space_stations(self) -> list[SpaceStation]:
        return [SpaceStation(
            id=749875195, planet_index=127, flags=1,
            election_ends_at=self._started + timedelta(hours=4),
            tactical_actions=[
                TacticalAction(
                    id=4091660627, name="模拟飞鹰风暴", status=1,
                    expires_at=self._started + timedelta(hours=12),
                    strategic_description="离线演习：为驻留星球提供模拟支援。",
                    costs=[TacticalCost(3992382197, 250, 1000, 75, 86400)],
                ),
                TacticalAction(id=3248573007, name="模拟轨道封锁", status=2,
                               expires_at=self._started + timedelta(hours=3)),
            ],
        )]

    async def get_dss_votes(self) -> list[DSSElection]:
        return [DSSElection(
            id="mock-election", context=801, status=1,
            options=[
                DSSVoteOption(127, 1200, "天使进取"),
                DSSVoteOption(64, 800, "梅里迪亚"),
            ],
        )]

    async def get_global_events(self) -> list[GlobalEvent]:
        return [GlobalEvent(
            id=9001, title="模拟银河事件", message="这是离线开发演习，不代表当前真实战况。",
            faction=Faction.HUMANS, planet_indices=(127,),
            expires_at=self._started + timedelta(days=1),
        )]

    async def get_episodes(self) -> list[Episode]:
        return [Episode(
            id=5001, title="模拟战役：民主长城", description="这是离线模拟控制中心。",
            faction=Faction.TERMINIDS, status=0,
            starts_at=self._started - timedelta(days=1),
            phases=[
                EpisodePhase(5101, status=2, intro_title="第一阶段：集结",
                             intro_message="集结演习部队。", outro_title="集结完成",
                             outro_message="模拟部队已准备就绪。"),
                EpisodePhase(5102, status=0, intro_title="第二阶段：防守",
                             intro_message="守住模拟天使进取战区。",
                             rewards=[EpisodeReward(897894480, 45)]),
            ],
        )]

    async def get_planet_regions(self) -> list[PlanetRegion]:
        return [PlanetRegion(
            planet_index=127, index=1, faction=Faction.TERMINIDS,
            health=2500, max_health=10000, players=25, available=True,
            regen_rate=0.5, damage_multiplier=1.5, region_size=2,
        )]

    async def get_special_units(self) -> list[SpecialUnit]:
        return [SpecialUnit("模拟 JET BRIGADE", Faction.AUTOMATONS, (196,), (1202,))]
