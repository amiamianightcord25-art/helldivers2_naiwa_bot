"""Parse CLI text and official QQ mention messages into the same command."""

import re
import unicodedata
from dataclasses import dataclass

from hd2bot.hd2.errors import CommandError

COMMANDS = frozenset({"战况", "主线", "星球", "进攻", "防守", "玩家", "战绩", "查战绩", "帮助",
                      "新闻", "战报", "补给线", "星图", "战线", "控制中心", "DSS", "公告",
                      "区域", "特殊部队", "更新", "补丁", "Steam在线", "个人指令", "DSS票数",
                      "超级商店", "战争债券", "看板", "更新日志", "订阅", "取消订阅", "订阅列表", "绑定推送"})
COMMANDS |= {"下载助手", "获取验证码", "同步帮助", "自我介绍", "推送设置", "开启推送", "暂停推送", "恢复推送", "绑定", "解绑", "获取战绩", "分享战绩", "关闭分享",
"百科", "武器", "战略配备", "盔甲", "强化资源", "装饰", "选择", "下一页", "上一页", "资料状态"}
COMMANDS |= {"签到", "我的等级", "等级表", "签到排行", "菜单", "战备英雄", "战备排行", "战备记录", "结束战备", "小贴士", "开源项目"}
ALIASES = {
    "源码": "开源项目", "github": "开源项目", "open_source": "开源项目",
    "tips": "小贴士", "tip": "小贴士", "小貼士": "小贴士", "加载提示": "小贴士",
    "menu": "菜单", "指令面板": "菜单", "功能菜单": "菜单", "快捷指令": "菜单",
    "签到打卡": "签到", "打卡": "签到", "checkin": "签到", "簽到": "签到",
    "我也要签到": "签到",
    "我也要查询": "获取战绩",
    "等级": "我的等级", "个人等级": "我的等级", "rank": "我的等级",
    "随机战备": "战备英雄", "戰備英雄": "战备英雄", "stratagemhero": "战备英雄",
    "结束游戏": "结束战备", "退出游戏": "结束战备",
    "领取验证码": "获取验证码", "已打开助手": "获取验证码", "准备好了": "获取验证码",
    "下载": "下载助手", "同步战绩": "获取战绩", "同步失败": "同步帮助",
    "查询战绩": "战绩", "战绩查询": "战绩", "查詢戰績": "战绩", "查戰績": "战绩", "戰績": "战绩",
    "介绍": "自我介绍", "关闭推送": "暂停推送", "推送状态": "推送设置",
    "bind": "绑定", "unbind": "解绑",
    "map": "星图", "地图": "星图", "planet": "星球", "major_order": "主线",
    "dispatches": "新闻", "global_events": "公告", "全球事件": "公告",
    "dss": "DSS", "空间站": "DSS", "control_centre": "控制中心", "warfront": "战线",
    "steam": "更新", "steam在线": "Steam在线", "subfaction": "特殊部队",
    "personal_order": "个人指令", "dss_votes": "DSS票数", "superstore": "超级商店",
    "warbonds": "战争债券", "dashboard": "看板", "战略看板": "看板",
    "changelog": "更新日志", "help": "帮助",
    "装备": "百科", "wiki": "百科", "装备百科": "百科",
    "武器列表": "武器", "weapon": "武器", "weapons": "武器",
    "战备": "战略配备", "stratagems": "战略配备", "护甲": "盔甲", "armor": "盔甲",
    "强化剂": "强化资源", "boosters": "强化资源", "装饰品": "装饰", "cosmetics": "装饰",
    "百科状态": "资料状态",
    "戰略配備": "战略配备", "護甲": "盔甲", "強化資源": "强化资源", "裝飾": "装饰",
    "選擇": "选择", "下一頁": "下一页", "上一頁": "上一页", "資料狀態": "资料状态",
}
_MENTION = re.compile(r"^(?:<@!?[0-9]+>|@机器人)\s*")


@dataclass(frozen=True)
class Command:
    name: str
    argument: str = ""


def _strip_mentions(value: str) -> str:
    while match := _MENTION.match(value):
        value = value[match.end():]
    return value


def command_text(text: str) -> str:
    """Normalize the envelope without interpreting arrow characters as markup."""
    if not isinstance(text, str):
        raise CommandError("请输入文字命令，输入“帮助”查看用法。")
    # Vertical caret glyphs otherwise normalize to sideways angle brackets.
    value = unicodedata.normalize("NFKC", text.translate(str.maketrans({
        "︿": "↑", "﹀": "↓",
    }))).strip()
    value = _strip_mentions(value)
    if value.startswith("/"):
        value = _strip_mentions(value[1:].lstrip())
    return value


def parse_command(text: str) -> Command:
    value = command_text(text)
    if value.startswith("HD2v1:"):
        return Command("绑定", value)
    if value.isascii() and value.isdecimal():
        return Command("选择", value)
    # Let users type 武器解放者 as well as 武器 解放者; exact aliases take priority.
    if value not in ALIASES and value not in COMMANDS:
        for prefix in ("战争债券", "战略配备", "强化资源", "武器", "盔甲", "装饰", "百科"):
            if value.startswith(prefix) and len(value) > len(prefix):
                value = prefix + " " + value[len(prefix):].strip()
                break
    pieces = value.split(maxsplit=1)
    if not pieces:
        raise CommandError("请输入命令，例如：战况。输入“帮助”查看全部命令。")
    name = ALIASES.get(pieces[0].casefold(), pieces[0])
    if name not in COMMANDS:
        raise CommandError("未识别的命令。可用：战况、主线、星球、星图、进攻、防守、玩家、战绩、新闻、补给线、帮助。")
    if name == "查战绩":
        name = "战绩"
    if name == "战报":
        name = "新闻"
    argument = " ".join(pieces[1].split()) if len(pieces) == 2 else ""
    if name in {"星球", "补给线"}:
        if not argument:
            raise CommandError(f"请输入星球名称或编号，例如：{name} Meridia。")
    elif name == "新闻" and argument:
        if (argument != "列表" and not re.fullmatch(r"列表\s+[1-9]\d{0,19}", argument)
                and not re.fullmatch(r"\d{1,20}", argument)):
            raise CommandError("新闻用法：新闻（最近列表）、新闻 列表 [页码]、新闻 <战报ID>。")
    elif name == "战绩" and argument:
        argument=argument.upper()
        if not re.fullmatch(r"HD2-[A-HJ-NP-Z2-9]{12}", argument):
            raise CommandError("请使用快照 ID，例如：战绩 HD2-ABCDEFGH2345；不支持按 SteamID 或昵称查询。")
    elif name in {"小贴士", "菜单", "战备英雄", "推送设置", "绑定", "订阅", "取消订阅", "绑定推送", "星图", "战线", "区域", "更新", "补丁",
                  "公告", "控制中心", "百科", "武器", "战略配备", "盔甲", "强化资源", "装饰", "战争债券", "选择"}:
        pass  # Validated by the relevant service, never used as a recipient ID.
    elif argument:
        raise CommandError(f"“{name}”不需要额外参数，请直接输入：{name}。")
    return Command(name=name, argument=argument)
