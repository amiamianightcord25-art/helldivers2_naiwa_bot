"""Small follow-up actions for completed replies; never derive targets from input."""

import re
from dataclasses import replace

from hd2bot.commands.parser import Command
from hd2bot.presentation import ChatContext, CommandButton, CommandReply

_SNAPSHOT_ID = re.compile(r"HD2-[A-HJ-NP-Z2-9]{12}")
_PAGE = re.compile(r"第\s*([1-9][0-9]{0,5})\s*/\s*([1-9][0-9]{0,5})\s*页")
_CATALOG_CATEGORIES = ("武器", "战略配备", "盔甲", "强化资源", "装饰", "战争债券")
_CATALOG_COMMANDS = {*_CATALOG_CATEGORIES, "百科", "选择", "下一页", "上一页"}
_READ_ACTIONS = {
    "DSS票数": (("DSS详情", "DSS"), ("再查票数", "DSS票数")),
    "DSS": (("迁移票数", "DSS票数"), ("刷新DSS", "DSS")),
    "战况": (("主要指令", "主线"), ("战略看板", "看板"), ("查看星图", "星图")),
    "主线": (("进攻战役", "进攻"), ("防守战役", "防守"), ("战略看板", "看板")),
    "进攻": (("主要指令", "主线"), ("防守战役", "防守"), ("查看星图", "星图")),
    "防守": (("主要指令", "主线"), ("进攻战役", "进攻"), ("查看星图", "星图")),
    "看板": (("主要指令", "主线"), ("DSS详情", "DSS"), ("查看星图", "星图")),
    "星图": (("战略看板", "看板"), ("进攻战役", "进攻"), ("防守战役", "防守")),
    "星球": (("银河星图", "星图"), ("战略看板", "看板")),
    "补给线": (("银河星图", "星图"), ("进攻战役", "进攻")),
    "玩家": (("银河战况", "战况"), ("Steam在线", "Steam在线")),
    "新闻": (("战报列表", "新闻"), ("银河公告", "公告")),
    "公告": (("战报列表", "新闻"), ("战略看板", "看板")),
    "更新": (("查看补丁", "补丁"), ("Steam在线", "Steam在线")),
    "补丁": (("游戏动态", "更新"), ("Steam在线", "Steam在线")),
    "Steam在线": (("全服玩家", "玩家"), ("游戏动态", "更新")),
    "控制中心": (("战略看板", "看板"), ("银河战况", "战况")),
    "特殊部队": (("战略看板", "看板"), ("银河星图", "星图")),
    "区域": (("战略看板", "看板"), ("银河星图", "星图")),
    "帮助": (("功能菜单", "菜单"), ("小贴士", "小贴士")),
    "更新日志": (("功能菜单", "菜单"), ("小贴士", "小贴士")),
    "开源项目": (("功能菜单", "菜单"),),
}


def _with_buttons(reply: CommandReply, *buttons: tuple[str, str]) -> CommandReply:
    return replace(reply, keyboard=(tuple(CommandButton(*button) for button in buttons),))


def _catalog_actions(reply: CommandReply, command: Command,
                     context: ChatContext | None) -> CommandReply:
    page = _PAGE.search(reply.text)
    if page is not None and "下一页 / 上一页 翻页" in reply.text:
        current, total = map(int, page.groups())
        if current <= total:
            buttons = []
            if current > 1:
                buttons.append(("上一页", "上一页"))
            if current < total:
                buttons.append(("下一页", "下一页"))
            # Page state belongs to the member who opened this list. Other
            # members can open their own catalogue with the unrestricted menu.
            owner = ((context.user_id,) if context is not None
                     and context.scope in {"group", "channel"} and context.user_id else ())
            if context is not None and context.scope in {"group", "channel"} and not owner:
                buttons = []
            return replace(reply, keyboard=(
                (*tuple(CommandButton(label, data, owner) for label, data in buttons),
                 CommandButton("百科菜单", "百科")),
            ))

    # Detail replies can be text-only. Attribution distinguishes a real entry
    # from an invalid query which may repeat arbitrary user-supplied wording.
    detail = ((reply.card is not None and reply.card.eyebrow == "HELLDIVERS 2 / WIKI")
              or "Helldivers Wiki 贡献者 ·" in reply.text)
    if detail and page is None:
        category = command.name if command.name in _CATALOG_CATEGORIES else ""
        if not category:
            subtitle = reply.card.subtitle if reply.card else "\n".join(reply.text.splitlines()[1:2])
            category = next((name for name in _CATALOG_CATEGORIES
                             if name in subtitle.split(" · ")), "")
        if category:
            return _with_buttons(reply, (category + "目录", category + " 列表"),
                                 ("百科菜单", "百科"))
        return _with_buttons(reply, ("百科菜单", "百科"))
    if command.name == "百科" and not command.argument and reply.text.startswith("HELLDIVERS 2 装备百科"):
        return _with_buttons(reply, ("武器目录", "武器 列表"),
                             ("战备目录", "战略配备 列表"), ("盔甲目录", "盔甲 列表"))
    return reply


def attach_actions(reply: CommandReply, command: Command,
                   context: ChatContext | None) -> CommandReply:
    """Keep reply content and existing actions, adding only useful known routes."""
    if reply.keyboard:
        return reply
    if context is not None and context.scope not in {"c2c", "dms", "group", "channel"}:
        return reply

    name = command.name
    if name == "战绩":
        if (context is None or not context.user_id or reply.card is None
                or reply.card.eyebrow != "HELLDIVERS 2 / CAREER"):
            return reply
        if context.scope in {"group", "channel"} and _SNAPSHOT_ID.fullmatch(command.argument):
            return _with_buttons(reply, ("我也要查询", "获取战绩"))
        if context.is_private and not command.argument:
            return _with_buttons(reply, ("更新战绩", "获取战绩"))
        return reply

    private = context is not None and context.is_private and bool(context.user_id)
    if (private and not command.argument and name in {"获取战绩", "下载助手", "绑定"}
            and reply.text.startswith("【第一次同步战绩 · 3 步完成】")):
        return _with_buttons(reply, ("助手已打开", "获取验证码"), ("同步帮助", "同步帮助"))
    if (private and name == "获取验证码" and not command.argument
            and reply.text.startswith("【第 2 步：验证码 ")):
        return _with_buttons(reply, ("同步帮助", "同步帮助"))

    if name in _CATALOG_COMMANDS:
        return _catalog_actions(reply, command, context)
    if name in _READ_ACTIONS:
        return _with_buttons(reply, *_READ_ACTIONS[name])
    return reply
