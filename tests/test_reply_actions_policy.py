import pytest

from hd2bot.career.onboarding import CareerOnboarding
from hd2bot.commands.parser import Command, parse_command
from hd2bot.presentation import ChatContext, CommandButton, CommandReply
from hd2bot.rendering.models import QueryCard
from hd2bot.services.reply_actions import attach_actions

SNAPSHOT = "HD2-ABCDEFGH2345"
CAREER = QueryCard("Original player", eyebrow="HELLDIVERS 2 / CAREER")


def buttons(reply):
    return [(button.label, button.command) for row in reply.keyboard for button in row]


@pytest.mark.parametrize("scope", ["group", "channel"])
def test_shared_career_cta_keeps_original_reply_but_never_reuses_identity(scope):
    original = CommandReply("Original player snapshot " + SNAPSHOT, CAREER)
    reply = attach_actions(original, Command("战绩", SNAPSHOT),
                           ChatContext(scope, "group-target", "original-sender"))
    assert reply.text == original.text and reply.card is original.card
    assert original.keyboard == ()
    assert buttons(reply) == [("我也要查询", "获取战绩")]
    assert SNAPSHOT not in str(reply.keyboard)
    assert "original-sender" not in str(reply.keyboard)
    assert "group-target" not in str(reply.keyboard)


@pytest.mark.parametrize("argument,reply", [
    ("", CommandReply("请进入私聊获取战绩")),
    (SNAPSHOT, CommandReply("快照已过期")),
    ("HD2-INVALID", CommandReply("无效快照", CAREER)),
    ("HD2v1:" + "a" * 200, CommandReply("提交失败")),
])
def test_group_guides_and_unsuccessful_snapshots_do_not_get_query_cta(argument, reply):
    assert attach_actions(reply, Command("战绩", argument), ChatContext("group", "g", "u")) is reply


@pytest.mark.parametrize("scope", ["c2c", "dms"])
def test_private_own_career_only_gets_update_action(scope):
    context = ChatContext(scope, "target", "owner")
    reply = CommandReply("完整战绩", CAREER)
    assert buttons(attach_actions(reply, Command("战绩"), context)) == [("更新战绩", "获取战绩")]
    assert attach_actions(reply, Command("战绩", SNAPSHOT), context) is reply


@pytest.mark.parametrize("scope", ["c2c", "dms"])
@pytest.mark.parametrize("name", ["获取战绩", "下载助手", "绑定"])
def test_preparation_guide_offers_explicit_ready_step(tmp_path, scope, name):
    reply = CommandReply(CareerOnboarding(tmp_path / "absent.json").guide())
    result = attach_actions(reply, Command(name), ChatContext(scope, "t", "u"))
    assert result.text == reply.text
    assert buttons(result) == [("助手已打开", "获取验证码"), ("同步帮助", "同步帮助")]


@pytest.mark.parametrize("scope", ["group", "channel"])
@pytest.mark.parametrize("name", ["获取战绩", "下载助手", "绑定", "获取验证码"])
def test_public_onboarding_does_not_loop_back_to_same_guide(scope, name):
    reply = CommandReply(CareerOnboarding.group_guide())
    assert attach_actions(reply, Command(name), ChatContext(scope, "g", "u")) is reply


def test_challenge_only_offers_help_never_another_challenge():
    reply = CommandReply(CareerOnboarding.challenge("AB3K9M"))
    result = attach_actions(reply, Command("获取验证码"), ChatContext("c2c", "u", "u"))
    assert buttons(result) == [("同步帮助", "同步帮助")]
    assert "AB3K9M" not in str(result.keyboard)


@pytest.mark.parametrize("command", [
    Command("绑定", "HD2v1:" + "a" * 200), Command("解绑"),
    Command("分享战绩"), Command("关闭分享"), Command("开启推送"), Command("订阅", "DSS"),
])
def test_mutations_never_get_repeat_buttons(command):
    reply = CommandReply("成功", CAREER)
    assert attach_actions(reply, command, ChatContext("c2c", "u", "u")) is reply


@pytest.mark.parametrize("name", ["签到", "战备英雄", "小贴士", "DSS"])
def test_existing_keyboard_is_preserved_exactly(name):
    original = CommandReply("已有结果", CAREER, ((CommandButton("已有按钮", "签到"),),))
    assert attach_actions(original, Command(name), ChatContext("group", "g", "u")) is original


@pytest.mark.parametrize("page,total,expected", [
    (1, 3, [("下一页", "下一页"), ("百科菜单", "百科")]),
    (2, 3, [("上一页", "上一页"), ("下一页", "下一页"), ("百科菜单", "百科")]),
    (3, 3, [("上一页", "上一页"), ("百科菜单", "百科")]),
    (1, 1, [("百科菜单", "百科")]),
])
def test_catalog_pages_only_offer_available_directions(page, total, expected):
    reply = CommandReply(f"武器目录 · 第 {page}/{total} 页 · 共 20 项\n"
                         "发送 选择 <本页序号> 查看详情；下一页 / 上一页 翻页。")
    assert buttons(attach_actions(reply, Command("武器", "列表"),
                                  ChatContext("c2c", "u", "u"))) == expected


@pytest.mark.parametrize("scope", ["group", "channel"])
def test_public_catalog_page_buttons_belong_to_the_requesting_member(scope):
    reply = CommandReply("武器目录 · 第 2/3 页\n下一页 / 上一页 翻页。")
    result = attach_actions(reply, Command("下一页"), ChatContext(scope, "room", "caller"))
    previous, following, catalogue = result.keyboard[0]
    assert previous.user_ids == following.user_ids == ("caller",)
    assert catalogue.user_ids == ()
    shared = attach_actions(CommandReply("战绩", CAREER), Command("战绩", SNAPSHOT),
                            ChatContext(scope, "room", "caller"))
    assert shared.keyboard[0][0].user_ids == ()


def test_public_catalog_without_caller_only_offers_a_new_catalogue():
    reply = CommandReply("武器目录 · 第 1/3 页\n下一页 / 上一页 翻页。")
    result = attach_actions(reply, Command("武器", "列表"), ChatContext("group", "room"))
    assert buttons(result) == [("百科菜单", "百科")]


@pytest.mark.parametrize("text", [
    "没有找到“第 1/3 页”。", "武器目录 · 第 1/3 页\n请直接查询名称。",
    "目录 · 第 4/3 页\n下一页 / 上一页 翻页", "已经是最后一页。",
])
def test_catalog_errors_and_unremembered_lists_do_not_offer_pagination(text):
    reply = CommandReply(text)
    assert attach_actions(reply, Command("下一页"), ChatContext("c2c", "u", "u")) is reply


@pytest.mark.parametrize("with_card", [False, True])
def test_catalog_detail_returns_static_directory_and_preserves_card(with_card):
    card = QueryCard("解放者", "AR-23 Liberator · 武器 · 突击步枪", "HELLDIVERS 2 / WIKI")
    reply = CommandReply("解放者\nAR-23 Liberator · 武器 · 突击步枪\nHelldivers Wiki 贡献者 · 来源",
                         card if with_card else None)
    result = attach_actions(reply, Command("选择", "1"), ChatContext("group", "g", "u"))
    assert result.card is reply.card and result.text == reply.text
    assert buttons(result) == [("武器目录", "武器 列表"), ("百科菜单", "百科")]


@pytest.mark.parametrize("name", [
    "DSS票数", "DSS", "战况", "主线", "进攻", "防守", "看板", "星图", "帮助", "更新日志",
])
def test_common_navigation_is_small_and_contains_valid_static_commands(name):
    reply = CommandReply("查询结果", QueryCard(name))
    result = attach_actions(reply, Command(name), ChatContext("group", "g", "u"))
    assert result.text == reply.text and result.card is reply.card
    assert 2 <= len(buttons(result)) <= 3
    for _, action in buttons(result):
        assert parse_command(action)


def test_unrelated_commands_and_unknown_contexts_are_unchanged():
    reply = CommandReply("未知内容")
    assert attach_actions(reply, Command("unknown"), ChatContext("group", "g", "u")) is reply
    assert attach_actions(reply, Command("DSS"), ChatContext("unknown", "t", "u")) is reply
    assert attach_actions(reply, Command("战绩"), None) is reply
