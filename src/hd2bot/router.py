"""Commands depend on domain services, never on QQ or provider JSON."""

import logging
import re
import unicodedata

from hd2bot import formatter
from hd2bot.career.formatter import format_career, format_career_error
from hd2bot.career.models import SNAPSHOT_RETENTION_DAYS, CareerError
from hd2bot.career.onboarding import CareerOnboarding
from hd2bot.career.service import CareerService
from hd2bot.commands.parser import (
    ALIASES,
    CAREER_COMMANDS,
    PROACTIVE_COMMANDS,
    command_text,
    parse_command,
)
from hd2bot.commands.search import search_planets
from hd2bot.hd2.errors import CommandError, HD2APIError
from hd2bot.hd2.service import DataResult, HD2Service
from hd2bot.presentation import (
    ChatContext,
    CommandButton,
    CommandReply,
    campaign_card,
    career_card,
    long_text_card,
    planet_card,
    war_card,
)
from hd2bot.project_info import OPEN_SOURCE_BRIEF, OPEN_SOURCE_NOTICE
from hd2bot.services.concurrency import gather_cancel_on_error

logger = logging.getLogger(__name__)
HELP = (
    "🦅 超级地球通讯终端\n"
    "菜单 / 指令面板：分类菜单与快捷按钮\n"
    "签到 · 我的等级 · 等级表 · 签到排行（机器人经验）\n"
    "战备英雄 / 随机战备：限时方向输入小游戏；战备记录 · 战备排行 · 结束战备\n"
    "小贴士：随机游戏加载提示；小贴士 <编号> · 小贴士 来源\n"
    "战况 · 主线 · 星球 <中文/英文名/编号>\n"
    "进攻 · 防守 · 玩家 · 帮助\n"
    "新闻 / 战报：最近列表 · 新闻 列表 <页码> · 新闻 <战报ID>\n"
    "补给线 <星球>\n"
    "星图 · 星图 <星球>：银河总览 / 局部补给图\n"
    "战线 [虫族/机器人/光能族] · 控制中心 · DSS · DSS票数 · 公告\n"
    "区域 [星球] · 特殊部队 · 更新 [新闻ID] · 补丁 · Steam在线\n"
    "更新日志 / changelog：查看最近一次机器人功能更新\n"
    "看板 / dashboard / 战略看板：公开银河战况总览\n"
    "百科 · 武器/战略配备/盔甲/强化资源/装饰 [名称或编号]\n"
    "战争债券 [名称]：本地 Wiki 快照与关联装备\n"
    "列表后回复序号选择；下一页 / 上一页 · 资料状态\n"
    "指令可加 /。例如：星球 Meridia\n\n" + OPEN_SOURCE_BRIEF
)
UNAVAILABLE = (
    "🚨 银河战争数据暂时无法取得。\n"
    "超级地球通讯网络可能正在维护，请稍后再试。"
)
FEATURE_OFFLINE = "该功能当前已下线。"


class CommandRouter:
    def __init__(self, service: HD2Service, career: CareerService | None = None,
                 notifications=None, steam=None, catalog=None, onboarding=None,
                 checkin=None, hero=None, menu=None, tips=None,
                 *, career_enabled=True, proactive_enabled=True):
        self.service = service
        self.career = career
        self.notifications = notifications
        self.steam = steam
        self.catalog = catalog
        self.onboarding = onboarding or CareerOnboarding()
        self.checkin = checkin
        self.hero = hero
        self.menu = menu
        self.tips = tips
        self.career_enabled = career_enabled
        self.proactive_enabled = proactive_enabled

    async def _hero_reply(self, text: str, context: ChatContext | None):
        if self.hero is None or context is None or not context.user_id:
            return None
        body = await self.hero.handle(
            text, scope=context.scope, target_id=context.target_id,
            user_id=context.user_id, display_name=context.display_name,
        )
        if body is None:
            return None
        return CommandReply(body, keyboard=(
            tuple(CommandButton(arrow, arrow) for arrow in ("↑", "↓", "←", "→")),
            (CommandButton("游戏状态", "战备英雄 状态"), CommandButton("结束游戏", "结束战备")),
        ))

    async def _optional(self, call):
        try:
            return await call
        except HD2APIError:
            return None

    async def handle(self, content: str) -> str:
        """Text entry point retained for the interactive CLI and tests."""
        return (await self.respond(content)).text

    async def respond(self, content: str, *, context: ChatContext | None = None) -> CommandReply:
        try:
            reply = await self._dispatch(content, context=context)
            try:
                command = parse_command(content)
            except CommandError:
                # Arrow-only input belongs to the arcade and already has its controls.
                return reply
            from hd2bot.services.reply_actions import attach_actions

            return attach_actions(reply, command, context)
        except CommandError as exc:
            return CommandReply(str(exc))
        except CareerError as exc:
            return CommandReply(format_career_error(exc))
        except HD2APIError:
            return CommandReply(UNAVAILABLE)
        except Exception as exc:
            logger.error("event=command_failed status=failed reason=%s", type(exc).__name__)
            return CommandReply("🚨 指令暂时处理失败，请稍后再试或发送 /帮助。")

    async def _dispatch(self, content: str, *, context: ChatContext | None = None) -> CommandReply:
        # Check the envelope before command parsing/search can echo user input.
        if (not self.career_enabled and isinstance(content, str)
                and re.search(r"HD2v1\s*[:：]", unicodedata.normalize("NFKC", content), re.IGNORECASE)):
            return CommandReply(FEATURE_OFFLINE)
        if (self.career_enabled and context is not None and not context.is_private
                and isinstance(content, str)
                and re.search(r"HD2v1\s*[:：]", unicodedata.normalize("NFKC", content), re.IGNORECASE)):
            return CommandReply(self.onboarding.group_guide())
        if not self.career_enabled or not self.proactive_enabled:
            raw = command_text(content)
            head = raw.split(maxsplit=1)[0].casefold() if raw else ""
            normalized = ALIASES.get(head, head)
            if ((not self.career_enabled and normalized in CAREER_COMMANDS)
                    or (not self.proactive_enabled and normalized in PROACTIVE_COMMANDS)):
                return CommandReply(FEATURE_OFFLINE)
        try:
            command = parse_command(content)
        except CommandError:
            reply = await self._hero_reply(command_text(content), context)
            if reply is not None:
                return reply
            raise
        if (not self.career_enabled and command.name in CAREER_COMMANDS) or (
                not self.proactive_enabled and command.name in PROACTIVE_COMMANDS):
            return CommandReply(FEATURE_OFFLINE)
        results: list[DataResult] = []
        card = None
        if command.name == "菜单":
            if self.menu is None:
                from hd2bot.services.menu import MenuService
                self.menu = MenuService()
            return self.menu.reply(command.argument, context=context)
        if command.name == "开源项目":
            return CommandReply(OPEN_SOURCE_NOTICE)
        if command.name == "小贴士":
            if self.tips is None:
                from hd2bot.services.tips import TipsService

                self.tips = TipsService()
            return self.tips.reply(command.argument, context=context)
        if command.name in {"签到", "我的等级", "等级表", "签到排行"}:
            if self.checkin is None:
                return CommandReply("签到服务尚未初始化。请在 QQ 中发送 签到。")
            return await self.checkin.handle(command.name, context)
        if command.name in {"战备英雄", "战备排行", "战备记录", "结束战备"}:
            text = command.name + (" " + command.argument if command.argument else "")
            reply = await self._hero_reply(text, context)
            return reply or CommandReply("请在私聊、群聊或频道中发送 战备英雄；群聊和频道需 @机器人。")
        if command.name == "自我介绍":
            from hd2bot.qq.welcome import GROUP_WELCOME, PRIVATE_WELCOME
            return CommandReply(GROUP_WELCOME if context and context.scope == "group" else PRIVATE_WELCOME)
        if command.name in {"推送设置", "开启推送", "暂停推送", "恢复推送"}:
            if context is None or context.scope != "group":
                return CommandReply("请在目标QQ群内 @机器人 使用群推送设置。")
            access = getattr(self.notifications, "access", None) if self.notifications is not None else None
            if access is None or not callable(getattr(access, "configure", None)):
                return CommandReply("群推送设置尚未配置，请联系维护者。")
            return CommandReply(await access.configure(command.name, command.argument, context))
        if command.name == "帮助":
            return CommandReply(HELP)
        if command.name == "更新日志":
            notices = (getattr(self.notifications, "release_notices", None)
                       if self.notifications is not None else None)
            return CommandReply(notices.command_text() if notices is not None
                                else "当前没有可用的机器人更新日志。")
        if command.name == "看板":
            from hd2bot.dashboard import dashboard_reply

            return await dashboard_reply(self.service)
        if command.name in {"百科", "武器", "战略配备", "盔甲", "强化资源", "装饰", "战争债券",
                            "选择", "下一页", "上一页", "资料状态"}:
            if self.catalog is None:
                from hd2bot.wiki.service import CatalogService
                self.catalog = CatalogService()
            return self.catalog.handle(command.name, command.argument, context=context)
        if command.name in {"个人指令", "超级商店"}:
            return CommandReply(f"{command.name}需要额外的游戏或第三方授权数据，当前尚未接入可靠来源。"
                                "\n可发送 帮助 查看已可用查询。")
        if command.name == "DSS票数":
            from hd2bot.dss_cards import dss_votes_card
            from hd2bot.galactic_features import format_dss_votes

            try:
                data = await self.service.get_dss_votes()
            except HD2APIError:
                return CommandReply("DSS票数暂时无法取得，请稍后重试。")
            # Keep compatibility with lightweight test/dry-run services that
            # do not implement the optional public election resource yet.
            if not isinstance(data, DataResult):
                return CommandReply("DSS票数当前尚未接入可靠来源。\n可发送 帮助查看已可用查询。")
            body = format_dss_votes(data.value)
            return CommandReply(formatter.finish_response(body, [data]),
                                dss_votes_card(data.value, [data]))
        if command.name in {"更新", "补丁", "Steam在线"}:
            if self.steam is None:
                return CommandReply("Steam 查询服务尚未初始化，请稍后重试。")
            if command.name == "Steam在线":
                return await self.steam.players()
            return await self.steam.query(command.argument, patches_only=command.name == "补丁")
        if command.name in {"订阅", "取消订阅", "订阅列表", "绑定推送"}:
            if context is not None and context.scope in {"dms", "channel"}:
                return CommandReply("频道私信已支持查询；主动订阅仍请在机器人的普通 QQ 单聊中管理。")
            if context is None or self.notifications is None:
                return CommandReply("请在指定 QQ 用户的已绑定私聊中使用订阅命令，CLI 不会创建消息订阅。")
            access = getattr(self.notifications, "access", None)
            if (context.scope == "group" and command.name in {"订阅", "取消订阅"}
                    and access is not None and callable(getattr(access, "can_manage", None))
                    and not await access.can_manage(context)):
                return CommandReply("群订阅由群主、管理员或维护者配置的管理用户操作。可发送 订阅列表 查看当前设置。")
            if command.name == "绑定推送":
                access = self.notifications.access
                if access is None:
                    return CommandReply("当前未配置推送用户绑定。")
                return CommandReply(await access.bind(command.argument, context.scope, context.target_id))
            return CommandReply(await self.notifications.handle_command(
                command.name, command.argument, context.scope, context.target_id,
            ))
        if command.name in {"获取战绩", "下载助手", "同步帮助"} or (command.name == "绑定" and not command.argument):
            if context is not None and not context.is_private:
                return CommandReply(self.onboarding.group_guide())
            return CommandReply(self.onboarding.troubleshooting() if command.name == "同步帮助" else self.onboarding.guide())
        if command.name == "战绩" and command.argument:
            if self.career is None:
                raise CareerError("not_configured")
            if context is None or not context.user_id:
                return CommandReply("请在私聊或群聊中发送 战绩 <快照ID>。")
            stats=await self.career.query_shared(command.argument,
                context.career_user_id,
                "private" if context.is_private else "group")
            return CommandReply(format_career(stats),career_card(stats))
        if command.name in {"分享战绩", "关闭分享"}:
            if context is None or not context.is_private or not context.user_id:
                return CommandReply("请私聊机器人管理自己的分享 ID。")
            if self.career is None:
                raise CareerError("not_configured")
            result=await self.career.sharing(context.career_user_id,command.name=="分享战绩")
            if command.name=="关闭分享":
                return CommandReply("已关闭分享，旧 ID 立即失效；本人仍可私聊查看快照。")
            return CommandReply("新的快照 ID："+result["shareId"]+"\n群聊 @机器人 战绩 "+result["shareId"]+
                                f"\n获得此 ID 的人可查看快照。旧 ID 已失效，{SNAPSHOT_RETENTION_DAYS} 天从采集时间计算。")
        if command.name in {"获取验证码", "绑定", "解绑", "战绩"}:
            if context is not None and not context.is_private:
                return CommandReply(self.onboarding.group_guide())
            if self.career is None:
                raise CareerError("not_configured")
            if command.name in {"获取验证码", "绑定", "解绑"}:
                if context is None or not context.user_id:
                    return CommandReply("请在机器人私聊中发送 /获取战绩。")
                user = context.career_user_id
                if command.name == "获取验证码":
                    issued = await self.career.challenge(user)
                    return CommandReply(self.onboarding.challenge(issued["challenge"]))
                if command.name == "解绑":
                    await self.career.unbind(user)
                    return CommandReply("已解绑并删除保存的战绩快照。需要再次查询时，请重新生成并同步。")
                if not re.fullmatch(r"HD2v1:[A-Za-z0-9_-]{160,6000}", command.argument):
                    return CommandReply("请先发送 获取战绩 下载并打开助手，再发送 获取验证码。将助手生成的完整 HD2v1: 胶囊直接发回本私聊。")
                await self.career.bind(user, command.argument)
                stats = await self.career.query_user(user)
                return CommandReply("同步成功，胶囊和验证码已使用，凭据已清除。\n" + format_career(stats),
                                    career_card(stats))
            if context is None:
                stats = await self.career.query()
            elif not context.user_id:
                return CommandReply("无法识别当前私聊账号。")
            else:
                stats = await self.career.query_user(context.career_user_id)
            return CommandReply(format_career(stats), career_card(stats))
        if command.name == "战况":
            war, planets, campaigns = await gather_cancel_on_error(
                self.service.get_war(), self._optional(self.service.get_planets()),
                self._optional(self.service.get_campaigns()),
            )
            results = [result for result in (war, planets, campaigns) if result is not None]
            body = formatter.format_war_status(
                war.value, planets.value if planets else None, campaigns.value if campaigns else None,
            )
            card = war_card(war.value, planets.value if planets else None,
                            campaigns.value if campaigns else None, results)
        elif command.name == "主线":
            orders, planets = await gather_cancel_on_error(
                self.service.get_major_order(), self._optional(self.service.get_planets()),
            )
            results = [result for result in (orders, planets) if result is not None]
            body = formatter.format_major_order(orders.value, planets.value if planets else None)
            card = long_text_card("主要指令", body, results)
        elif command.name == "星图":
            from hd2bot.starmap import map_reply

            planets = await self.service.get_planets()
            focus = None
            if command.argument:
                found = search_planets(command.argument, planets.value)
                if not found.match:
                    body = ("未找到唯一匹配的星球，你可以查询：\n" + "\n".join(
                        f"• 星图 {p.name}（#{p.index}）" for p in found.candidates
                    ) if found.candidates else "未找到该星球。请尝试：星图 Meridia，或直接发送 星图。")
                    return CommandReply(formatter.finish_response(body, [planets]))
                focus = found.match
            campaigns, orders = await gather_cancel_on_error(
                self._optional(self.service.get_campaigns()),
                self._optional(self.service.get_major_order()),
            )
            results = [r for r in (planets, campaigns, orders) if r is not None]
            body, card = map_reply(planets.value, campaigns.value if campaigns else None,
                                   orders.value if orders else None, results, focus)
        elif command.name == "战线":
            from hd2bot.warfront import format_front, parse_front

            faction = parse_front(command.argument)
            planets, campaigns, orders = await gather_cancel_on_error(
                self.service.get_planets(), self.service.get_campaigns(),
                self._optional(self.service.get_major_order()),
            )
            results = [r for r in (planets, campaigns, orders) if r is not None]
            body = format_front(planets.value, campaigns.value, orders.value if orders else None,
                                faction)
            card = long_text_card("阵营战线", body, results)
        elif command.name in {"DSS", "公告", "控制中心", "特殊部队", "区域"}:
            from hd2bot.galactic_features import (
                format_episodes,
                format_global_events,
                format_planet_regions,
                format_space_stations,
                format_special_units,
            )

            selection = None
            if command.name in {"公告", "控制中心"} and command.argument:
                if (not command.argument.isascii() or not command.argument.isdecimal()
                        or len(command.argument) > 10):
                    raise CommandError(f"用法：{command.name}（总览），或 {command.name} <数字ID>（详情）。")
                selection = int(command.argument)
            if command.name == "区域" and command.argument:
                planets = await self.service.get_planets()
                results.append(planets)
                found = search_planets(command.argument, planets.value)
                if not found.match:
                    body = ("未找到唯一匹配的星球，你可以查询：\n" + "\n".join(
                        f"• 区域 {p.name}（#{p.index}）" for p in found.candidates
                    ) if found.candidates else "未找到该星球，请使用中文名、英文名或编号。")
                    return CommandReply(formatter.finish_response(body, results))
                selection = found.match.index
            methods = {
                "DSS": ("get_space_stations", format_space_stations),
                "公告": ("get_global_events", format_global_events),
                "控制中心": ("get_episodes", format_episodes),
                "特殊部队": ("get_special_units", format_special_units),
                "区域": ("get_planet_regions", format_planet_regions),
            }
            method, format_body = methods[command.name]
            data = await getattr(self.service, method)()
            results.append(data)
            body = format_body(data.value, selection) if command.name in {
                "公告", "控制中心", "区域",
            } else format_body(data.value)
            if command.name == "DSS":
                from hd2bot.dss_cards import dss_station_card

                card = dss_station_card(data.value, results)
            elif len(body) >= 450 and command.name in {"区域", "控制中心"}:
                from hd2bot.feature_cards import episode_card, region_card

                build_card = region_card if command.name == "区域" else episode_card
                card = build_card(data.value, results, selection)
            else:
                card = long_text_card(command.name, body, results)
        elif command.name in {"星球", "补给线"}:
            planets = await self.service.get_planets()
            results = [planets]
            found = search_planets(command.argument, planets.value)
            if found.match:
                if command.name == "补给线":
                    from hd2bot.intelligence import format_supply_lines
                    body = format_supply_lines(found.match, planets.value)
                    card = long_text_card("补给线 · " + found.match.name, body, results)
                else:
                    body = formatter.format_planet(found.match)
                    card = planet_card(found.match, results)
            elif found.candidates:
                body = "未找到唯一匹配的星球，你可以查询：\n" + "\n".join(
                    f"• {planet.name} ({planet.english_name or planet.index})"
                    for planet in found.candidates
                )
            else:
                body = "未找到该星球。请尝试中文名、英文名或星球编号。"
        elif command.name == "进攻":
            campaigns, orders = await gather_cancel_on_error(
                self.service.get_campaigns(), self._optional(self.service.get_major_order()),
            )
            results = [result for result in (campaigns, orders) if result is not None]
            body = formatter.format_campaigns(campaigns.value, orders.value if orders else None)
            card = campaign_card(campaigns.value, orders.value if orders else None, results)
        elif command.name == "防守":
            planets = await self.service.get_planets()
            results = [planets]
            body = formatter.format_defenses(planets.value)
            card = long_text_card("防守战役", body, results)
        elif command.name == "新闻":
            from hd2bot.intelligence import (
                NEWS_PAGE_SIZE,
                format_dispatch_detail,
                format_dispatches,
                parse_dispatch_argument,
            )
            news = await self.service.get_dispatches()
            results = [news]
            mode, value = parse_dispatch_argument(command.argument)
            if mode == "detail":
                body = format_dispatch_detail(news.value, value)
                if body is None:
                    body = (f"未找到战报 #{value}。\n"
                            "可发送 新闻 列表 查看最近 25 条战报。")
            else:
                body = format_dispatches(news.value, page=value, page_size=NEWS_PAGE_SIZE,
                                         history_limit=25)
            card = long_text_card("银河新闻", body, results, threshold=650)
        else:
            stats = await self.service.get_statistics()
            results = [stats]
            body = formatter.format_players(stats.value)
        return CommandReply(formatter.finish_response(body, results), card)
