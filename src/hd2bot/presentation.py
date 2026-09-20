"""Domain-driven cards and text alternatives; independent of NoneBot and Chromium."""

from dataclasses import dataclass, field

from hd2bot import formatter as f
from hd2bot.career.formatter import GROUPS, account_label, data_notice, data_status
from hd2bot.career.models import SNAPSHOT_RETENTION_DAYS, CareerStats
from hd2bot.hd2.models import Campaign, MajorOrder, Planet, WarStatus
from hd2bot.hd2.service import DataResult
from hd2bot.rendering.models import CardMetric, CardRow, CardSection, QueryCard
from hd2bot.services.localization import clean_text, faction_name, term


@dataclass(frozen=True)
class ChatContext:
    scope: str
    target_id: str = field(repr=False)
    user_id: str | None = field(default=None, repr=False)
    group_role: str = field(default="member", repr=False)
    display_name: str = field(default="", repr=False, compare=False)

    @property
    def is_private(self) -> bool:
        return self.scope in {"c2c", "dms"}

    @property
    def career_user_id(self) -> str:
        # Guild and C2C identifiers are different identity domains.
        prefix = {"c2c": "qq:", "dms": "qqdms:", "group": "qqgroup:",
                  "channel": "qqchannel:"}.get(self.scope)
        if prefix is None or not self.user_id:
            raise ValueError("invalid_chat_identity")
        return prefix + self.user_id


@dataclass(frozen=True)
class CommandButton:
    label: str
    command: str
    user_ids: tuple[str, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class CommandReply:
    text: str
    card: QueryCard | None = None
    keyboard: tuple[tuple[CommandButton, ...], ...] = ()


def _metadata(results: list[DataResult]) -> dict:
    notices = []
    if any(result.stale for result in results):
        notices.append("数据暂时无法更新，以下为最近缓存")
    if any(result.source == "mock" for result in results):
        notices.append("模拟数据 · 非真实战况")
    labels = {"captured": "官方战局 API", "community": "社区战局 API", "mock": "本地模拟"}
    sources = " / ".join(dict.fromkeys(labels.get(r.source, "未知来源") for r in results))
    stamp = min(r.fetched_at for r in results).astimezone().isoformat(timespec="seconds")
    return {"notices": tuple(notices), "footer": (f"来源：{sources}", f"最早采样时间：{stamp}")}


def career_card(stats: CareerStats) -> QueryCard:
    metric_keys = (("totalEnemyKills", "敌人总击杀"), ("totalMissionsCompleted", "完成任务"),
                   ("totalSuccesfulExtractions", "成功撤离"), ("totalSamplesCollected", "收集样本"))
    sections = []
    battle = GROUPS[0][1] + GROUPS[1][1]
    for title, fields in (("作战记录", battle), GROUPS[2], GROUPS[3]):
        sections.append(CardSection(title, tuple(CardRow(label, f"{stats.values[key]:,}")
                                               for key, label in fields)))
    cached = []
    if stats.local_cached:
        cached.append("机器人本地缓存")
    if stats.cached:
        cached.append("服务端缓存")
    notices = ["模拟数据 · 非真实战绩"] if stats.mock else []
    if stats.snapshot:
        notices.append(
            f"快照从采集起仅保留 {SNAPSHOT_RETENTION_DAYS} 天，到期自动删除；查看不会续期。"
        )
    return QueryCard(
        stats.player_name or "未记录 Steam 昵称", "SteamID64：" + (stats.steam_id or "旧快照未记录，重新同步后补齐") +
        " · " + account_label(stats) + " · 完整 27 项", "HELLDIVERS 2 / CAREER",
        metrics=tuple(CardMetric(label, f"{stats.values[key]:,}") for key, label in metric_keys),
        sections=tuple(sections), notices=tuple(notices),
        footer=("数据状态：" + data_status(stats),
                ("快照采集时间：" if stats.snapshot else "原始查询时间：") + stats.queried_at.astimezone().isoformat(timespec="seconds"),
                data_notice(stats),
                *(('到期时间：'+stats.expires_at.astimezone().isoformat(timespec="seconds"),) if stats.expires_at else ())),
    )


def war_card(war: WarStatus, planets: list[Planet] | None,
             campaigns: list[Campaign] | None, results: list[DataResult]) -> QueryCard:
    earth = next((p for p in planets or [] if p.index == 0), None)
    active = None if campaigns is None else [c for c in campaigns if not c.planet.disabled
        and (c.planet.owner.value not in {"Humans", "Unknown"} or f._active_defense(c.planet))]
    metrics = (CardMetric("全服在线", f._number(war.players)),
               CardMetric("活跃战役", f._number(len(active) if active is not None else None)),
               CardMetric("进行中的防守", f._number(sum(f._active_defense(p) for p in planets)
                                                  if planets is not None else None)))
    top = sorted(active or [], key=lambda c: -(c.planet.players or 0))[:6]
    sections = [CardSection("超级地球", (
        CardRow("状态", f._planet_state(earth) if earth else "暂无数据"),
        CardRow("战争状态", f.format_war_status(war, None, None).splitlines()[1].split("：", 1)[1]),
    )), CardSection("热门战区", tuple(CardRow(f._name(c.planet), f._number(c.planet.players)+" 人",
        f._planet_state(c.planet), f._defense_progress(c.planet) if f._active_defense(c.planet)
        else c.planet.liberation) for c in top) or (CardRow("当前战役", "暂无数据"),))]
    if war.events:
        sections.append(CardSection("银河公告", tuple(CardRow(f"公告 {i}", clean_text(message))
                                                       for i, message in enumerate(war.events[:3], 1))))
    return QueryCard("银河战况", "超级地球战略情报", "HELLDIVERS 2 / GALACTIC WAR",
                     metrics, tuple(sections), **_metadata(results))


def planet_card(planet: Planet, results: list[DataResult]) -> QueryCard:
    defense = f._active_defense(planet)
    sections = [CardSection("星球状态", (
        CardRow("控制方", faction_name(planet.owner)),
        CardRow("作战状态", f._planet_state(planet), progress=f._defense_progress(planet)
                if defense else planet.liberation),
        CardRow("星球生命", f"{f._number(planet.health)} / {f._number(planet.max_health)}"),
        CardRow("生命恢复", f._number(planet.regen_rate, 2)+" HP/秒"
                if planet.regen_rate is not None else "暂无数据"),
    )), CardSection("环境与位置", (
        CardRow("星区", term(planet.sector)), CardRow("环境", term(planet.biome)),
        CardRow("环境影响", "、".join(term(h) for h in planet.hazards) or "暂无数据"),
        CardRow("部署状态", "当前不可部署" if planet.disabled else "以游戏内状态为准"),
    ))]
    if defense:
        event = planet.event
        sections.append(CardSection("防守事件", (
            CardRow("入侵方", faction_name(event.faction)),
            CardRow("防守进度", f._percent(f._defense_progress(planet)),
                    progress=f._defense_progress(planet)),
            CardRow("剩余时间", f._remaining(event.ends_at)),
            CardRow("事件生命", f"{f._number(event.health)} / {f._number(event.max_health)}"),
        )))
    return QueryCard(f._name(planet), planet.english_name or "星球情报", "HELLDIVERS 2 / PLANET",
        (CardMetric("星球编号", str(planet.index)), CardMetric("在线玩家", f._number(planet.players)),
         CardMetric("状态", "防守中" if defense else faction_name(planet.owner))),
        tuple(sections), **_metadata(results))


def campaign_card(campaigns: list[Campaign], orders: list[MajorOrder] | None,
                  results: list[DataResult]) -> QueryCard | None:
    active = f._attacks(campaigns, orders)
    if len(active) < 5:
        return None
    targets = f._target_indices(orders)
    sections = []
    for start in range(0, len(active), 10):
        rows = []
        for item in active[start:start+10]:
            p = item.planet
            priority = "主线 · " if p.index in targets else ""
            rows.append(CardRow(priority+f._name(p), f._percent(p.liberation),
                                f"{faction_name(p.owner)} · {f._number(p.players)} 人", p.liberation))
        sections.append(CardSection(f"战役 {start+1}–{min(start+10, len(active))}", tuple(rows)))
    return QueryCard("进攻战役", "主线目标优先，其次在线人数与解放进度", "HELLDIVERS 2 / CAMPAIGNS",
                     (CardMetric("当前进攻", str(len(active))),), tuple(sections), **_metadata(results))


def long_text_card(title: str, body: str, results: list[DataResult], *, threshold: int = 450):
    if len(body) < threshold:
        return None
    lines = body.splitlines()
    sections = []
    for start in range(1, len(lines), 10):
        rows = []
        for line in lines[start:start+10]:
            if not line.strip():
                continue
            label, separator, value = line.partition("：")
            rows.append(CardRow(label if separator else "", value if separator else line))
        sections.append(CardSection("情报" if not sections else "续", tuple(rows)))
    return QueryCard(title, "超级地球情报终端", sections=tuple(sections), **_metadata(results))
