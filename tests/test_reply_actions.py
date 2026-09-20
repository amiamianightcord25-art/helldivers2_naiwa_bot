"""Image and Markdown actions share QQ's bounded passive-message allowance."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from nonebot.adapters.qq.exception import ActionFailed, NetworkError
from nonebot.drivers import Response

from hd2bot.config import Settings
from hd2bot.presentation import CommandButton, CommandReply
from hd2bot.qq.adapter import QQDispatcher, snapshot_copy_text

_SCOPES = ["group", "c2c", "channel", "dms"]
_METHODS = {"group": "post_group_messages", "c2c": "post_c2c_messages",
            "channel": "post_messages", "dms": "post_dms_messages"}
_ACTIONS = ((CommandButton("我也要查询", "战绩"),),)


def rejection(status=400):
    return ActionFailed(Response(status, content=b'{"code":304036}'))


def url_rejection(code=304003):
    return ActionFailed(Response(400, content=(f'{{"code":{code}}}').encode()))


class Clock:
    def __init__(self):
        self.value = 0.0
        self.waits = []

    def __call__(self):
        return self.value

    async def sleep(self, delay):
        self.waits.append(delay)
        self.value += delay
        await asyncio.sleep(0)


@pytest.fixture
async def dispatcher():
    renderer = AsyncMock()
    renderer.render.return_value = b"image-bytes"
    clock = Clock()
    dispatcher = QQDispatcher(Settings(), AsyncMock(), renderer,
                              channel_clock=clock, channel_sleep=clock.sleep)
    yield dispatcher
    await dispatcher.close()


@pytest.fixture
def bot():
    bot = AsyncMock()
    bot.post_group_files.return_value = SimpleNamespace(file_info="file-data")
    bot.post_c2c_files.return_value = SimpleNamespace(file_info="file-data")
    return bot


def payloads(bot, scope):
    return [call.kwargs for call in getattr(bot, _METHODS[scope]).await_args_list]


def assert_image(payload, scope):
    if scope in {"group", "c2c"}:
        assert payload["msg_type"] == 7
        assert payload["media"].file_info == "file-data"
    else:
        assert payload["file_image"] == b"image-bytes"


@pytest.mark.parametrize("scope", _SCOPES)
async def test_image_is_preserved_and_actions_follow_as_short_markdown(dispatcher, bot, scope):
    reply = CommandReply("完整的战绩详情", object(), _ACTIONS)
    await dispatcher._reply(bot, scope, "target", "message", reply)
    sent = payloads(bot, scope)
    assert len(sent) == 2
    assert_image(sent[0], scope)
    assert sent[1]["markdown"].content == "可使用下方按钮继续查询。"
    button = sent[1]["keyboard"].content.rows[0].buttons[0]
    assert button.render_data.label == "我也要查询"
    assert button.action.data == "战绩"
    assert button.action.permission.type == 2
    assert button.action.enter == (scope == "c2c")
    assert all(item["msg_id"] == "message" for item in sent)
    if scope in {"group", "c2c"}:
        assert [item["msg_seq"] for item in sent] == [1, 2]
    else:
        assert all("msg_seq" not in item and "msg_type" not in item for item in sent)


@pytest.mark.parametrize("scope", _SCOPES)
async def test_snapshot_id_appears_in_one_copyable_message_with_buttons(dispatcher, bot, scope):
    text = "详细战绩\n快照 ID：HD2-ABCDEFGH2345\n群聊可用：战绩 HD2-ABCDEFGH2345"
    await dispatcher._reply(bot, scope, "target", "message", CommandReply(text, object(), _ACTIONS))
    sent = payloads(bot, scope)
    assert len(sent) == 2
    assert_image(sent[0], scope)
    assert sent[1]["markdown"].content == snapshot_copy_text(text)
    assert "content" not in sent[0] and "markdown" not in sent[0]


@pytest.mark.parametrize("scope", _SCOPES)
async def test_snapshot_markdown_rejection_falls_back_without_resending_image(dispatcher, bot, scope):
    getattr(bot, _METHODS[scope]).side_effect = [None, rejection(), None]
    text = "详细战绩\n快照 ID：HD2-ABCDEFGH2345"
    await dispatcher._reply(bot, scope, "target", "message", CommandReply(text, object(), _ACTIONS))
    sent = payloads(bot, scope)
    assert len(sent) == 3
    assert_image(sent[0], scope)
    assert "markdown" in sent[1]
    assert sent[2]["content"] == snapshot_copy_text(text)
    assert "详细战绩" not in sent[2]["content"]
    if scope in {"group", "c2c"}:
        assert [item["msg_seq"] for item in sent] == [1, 2, 3]


@pytest.mark.parametrize("scope", _SCOPES)
async def test_card_action_rejection_leaves_useful_plain_command(dispatcher, bot, scope):
    getattr(bot, _METHODS[scope]).side_effect = [None, rejection(), None]
    await dispatcher._reply(bot, scope, "target", "message", CommandReply("详情", object(), _ACTIONS))
    assert payloads(bot, scope)[-1]["content"] == "可直接发送：战绩"


@pytest.mark.parametrize("scope", _SCOPES)
@pytest.mark.parametrize("where", ["image", "markdown"])
@pytest.mark.parametrize("error", [NetworkError("unknown"), RuntimeError("unknown"), rejection(500)])
async def test_unknown_send_outcomes_never_blindly_retry(dispatcher, bot, scope, where, error):
    send = getattr(bot, _METHODS[scope])
    send.side_effect = [error] if where == "image" else [None, error]
    with pytest.raises(type(error)):
        await dispatcher._reply(bot, scope, "target", "message", CommandReply("详情", object(), _ACTIONS))
    assert send.await_count == (1 if where == "image" else 2)


@pytest.mark.parametrize("scope", _SCOPES)
async def test_definite_image_rejection_falls_back_to_text_with_actions(dispatcher, bot, scope):
    getattr(bot, _METHODS[scope]).side_effect = [rejection(), None]
    reply = CommandReply("完整战绩文本", object(), _ACTIONS)
    await dispatcher._reply(bot, scope, "target", "message", reply)
    sent = payloads(bot, scope)
    assert len(sent) == 2
    assert_image(sent[0], scope)
    assert sent[1]["markdown"].content == reply.text
    if scope in {"group", "c2c"}:
        assert [item["msg_seq"] for item in sent] == [1, 2]


@pytest.mark.parametrize("scope", ["group", "c2c"])
@pytest.mark.parametrize("failure", ["render", "upload", "file_info"])
async def test_before_send_image_failures_do_not_consume_sequence(dispatcher, bot, scope, failure):
    upload = bot.post_group_files if scope == "group" else bot.post_c2c_files
    if failure == "render":
        dispatcher.renderer.render.side_effect = RuntimeError("failed rendering")
    elif failure == "upload":
        upload.side_effect = NetworkError("upload result unknown, srv_send_msg is False")
    else:
        upload.return_value = SimpleNamespace(file_info="")
    await dispatcher._reply(bot, scope, "target", "message", CommandReply("详情", object(), _ACTIONS))
    sent = payloads(bot, scope)
    assert len(sent) == 1
    assert sent[0]["msg_seq"] == 1 and sent[0]["msg_type"] == 2


@pytest.mark.parametrize("scope,limit", [("group", 5), ("c2c", 4), ("channel", 4), ("dms", 4)])
@pytest.mark.parametrize("reject_image", [False, True])
async def test_long_text_keeps_separate_short_actions_and_stays_within_budget(
    dispatcher, bot, scope, limit, reject_image,
):
    send = getattr(bot, _METHODS[scope])

    async def reject_formats(**kwargs):
        if "markdown" in kwargs or "media" in kwargs or "file_image" in kwargs:
            raise rejection()

    send.side_effect = reject_formats
    reply = CommandReply("很长的查询结果🌍\n" * 2000,
                         object() if reject_image else None, _ACTIONS)
    await dispatcher._reply(bot, scope, "target", "message", reply)
    sent = payloads(bot, scope)
    assert len(sent) == limit
    markdown = next(item for item in sent if "markdown" in item)
    assert markdown["markdown"].content == "可使用下方按钮继续查询。"
    assert sent[-1]["content"] == "可直接发送：战绩"
    assert any("已缩略" in item.get("content", "") for item in sent)
    assert all(len(item.get("content", "").encode("utf-8")) <= 1800 for item in sent)
    if scope in {"group", "c2c"}:
        assert [item["msg_seq"] for item in sent] == list(range(1, limit + 1))


@pytest.mark.parametrize("bad_keyboard", [
    "invalid", ((),), ((object(),),), ((CommandButton("超长按钮名称超过十个字符了", "战绩"),),),
    ((CommandButton("查询", ""),),), ((CommandButton("查询", "x" * 1025),),),
    ((CommandButton("查询\n", "战绩"),),), ((CommandButton("查询", "战\n绩"),),),
    (_ACTIONS[0],) * 6, ((_ACTIONS[0][0],) * 6,),
])
@pytest.mark.parametrize("image", [False, True])
async def test_invalid_keyboard_never_loses_plain_or_image_content(dispatcher, bot, bad_keyboard, image):
    reply = CommandReply("完整结果", object() if image else None, bad_keyboard)
    await dispatcher._reply(bot, "group", "target", "message", reply)
    sent = payloads(bot, "group")
    assert len(sent) == 1
    assert sent[0]["msg_seq"] == 1
    if image:
        assert_image(sent[0], "group")
    else:
        assert sent[0]["content"] == "完整结果"


async def test_button_commands_are_never_truncated(dispatcher, bot):
    command = "战备 " + "A" * 200
    reply = CommandReply("请选择", keyboard=((CommandButton("指定战备", command),),))
    await dispatcher._reply(bot, "group", "target", "message", reply)
    button = payloads(bot, "group")[0]["keyboard"].content.rows[0].buttons[0]
    assert button.action.data == command


async def test_double_format_rejection_then_plain_failure_is_not_logged_as_sent(dispatcher, bot, caplog):
    bot.post_c2c_messages.side_effect = [rejection(), rejection(), NetworkError("unknown")]
    with caplog.at_level(logging.INFO, logger="hd2bot.qq.adapter"):
        with pytest.raises(NetworkError):
            await dispatcher._reply(bot, "c2c", "target", "message",
                                    CommandReply("详情", object(), _ACTIONS))
    assert "event=qq_reply_sent" not in caplog.text
    assert [item["msg_seq"] for item in payloads(bot, "c2c")] == [1, 2, 3]


@pytest.mark.parametrize("scope", _SCOPES)
@pytest.mark.parametrize("code", [304003, 40054010])
@pytest.mark.parametrize("keyboard", [(), _ACTIONS])
async def test_explicit_url_rejection_preserves_prose_without_links(dispatcher, bot, scope, code, keyboard):
    getattr(bot, _METHODS[scope]).side_effect = [url_rejection(code), None]
    reply = CommandReply("游戏小贴士为参考译文，来源：[Wiki](https://example.com/Tips)\n"
                         "英文原文出处：https://example.com/Loading\n战绩", keyboard=keyboard)
    await dispatcher._reply(bot, scope, "target", "message", reply)
    sent = payloads(bot, scope)
    assert len(sent) == 2
    assert "https://" not in sent[1]["content"]
    assert "游戏小贴士为参考译文，来源：Wiki" in sent[1]["content"]
    assert "英文原文出处：[链接已省略]" in sent[1]["content"]
    assert "战绩" in sent[1]["content"]
    if scope in {"group", "c2c"}:
        assert [item["msg_seq"] for item in sent] == [1, 2]


async def test_other_rejections_do_not_remove_urls(dispatcher, bot):
    bot.post_group_messages.side_effect = [rejection(), None]
    reply = CommandReply("原文 https://example.com/Tips 战绩", keyboard=_ACTIONS)
    await dispatcher._reply(bot, "group", "target", "message", reply)
    assert "https://example.com/Tips" in payloads(bot, "group")[-1]["content"]


async def test_url_fallback_preserves_following_punctuation_and_chinese_prose(dispatcher, bot):
    bot.post_group_messages.side_effect = [url_rejection(), None]
    reply = CommandReply("来源：https://example.com/Tips。中文为参考译文，非官方。")
    await dispatcher._reply(bot, "group", "target", "message", reply)
    assert payloads(bot, "group")[-1]["content"] == (
        "来源：[链接已省略]。中文为参考译文，非官方。"
    )


@pytest.mark.parametrize("scope,limit", [("group", 5), ("c2c", 4)])
async def test_plain_url_fallback_keeps_final_truncation_and_actions(dispatcher, bot, scope, limit):
    async def reject_urls(**kwargs):
        if "https://" in kwargs.get("content", ""):
            raise url_rejection()
        if "markdown" in kwargs:
            raise rejection()

    getattr(bot, _METHODS[scope]).side_effect = reject_urls
    reply = CommandReply(("原文 https://example.com/Tips\n" * 500), keyboard=_ACTIONS)
    await dispatcher._reply(bot, scope, "target", "message", reply)
    sent = payloads(bot, scope)
    assert len(sent) == limit
    assert [item["msg_seq"] for item in sent] == list(range(1, limit + 1))
    assert sum("https://" in item.get("content", "") for item in sent) == 1
    assert any("已缩略" in item.get("content", "") for item in sent[1:-2])
    assert sent[-2]["keyboard"].content.rows[0].buttons[0].action.data == "战绩"
    assert sent[-1]["content"] == "可直接发送：战绩"


@pytest.mark.parametrize("status", [None, 200])
@pytest.mark.parametrize("code", [50037, 50056, 304037, 304036, 304003, 40054010])
async def test_documented_format_errors_are_definite_even_without_http_4xx(
    dispatcher, bot, status, code,
):
    class PlatformError(Exception):
        status_code = status

        def __init__(self):
            self.code = code

    bot.post_group_messages.side_effect = [PlatformError(), None]
    reply = CommandReply("战绩查询提示", keyboard=_ACTIONS)
    await dispatcher._reply(bot, "group", "target", "message", reply)
    sent = payloads(bot, "group")
    assert len(sent) == 2
    assert sent[1]["content"] == "战绩查询提示"


async def test_concurrent_channel_images_markdown_and_fallback_share_five_per_second_queue(dispatcher, bot):
    clock = dispatcher._channel_clock
    attempts = []

    async def send(**kwargs):
        attempts.append((clock(), kwargs))
        await asyncio.sleep(0)
        if "markdown" in kwargs:
            raise rejection()

    bot.post_messages.side_effect = send
    await asyncio.gather(*(
        dispatcher._reply(bot, "channel", f"target-{index % 2}", f"message-{index}",
                          CommandReply("详情", object(), _ACTIONS))
        for index in range(4)
    ))
    assert len(attempts) == 12
    stamps = [stamp for stamp, _ in attempts]
    assert all(later - earlier >= 0.209 for earlier, later in zip(stamps, stamps[1:]))
    assert all(sum(start <= stamp < start + 1 for stamp in stamps) <= 5 for start in stamps)
    assert sum("file_image" in payload for _, payload in attempts) == 4
    assert sum("markdown" in payload for _, payload in attempts) == 4
    assert sum("content" in payload for _, payload in attempts) == 4
    assert len(clock.waits) == 11


@pytest.mark.parametrize("scope", ["group", "c2c", "dms"])
async def test_channel_throttle_does_not_delay_other_scopes(dispatcher, bot, scope):
    dispatcher._channel_next_send = 1000
    await dispatcher._reply(bot, scope, "target", "message", CommandReply("详情", object(), _ACTIONS))
    assert len(payloads(bot, scope)) == 2
    assert dispatcher._channel_clock.value == 0
    assert not dispatcher._channel_clock.waits


async def test_scoped_buttons_preserve_exact_user_ids_and_public_buttons_stay_public(dispatcher, bot):
    users = ("member-a", "member-b")
    reply = CommandReply("翻页", keyboard=((
        CommandButton("下一页", "下一页", user_ids=users),
        CommandButton("查战绩", "战绩"),
    ),))
    await dispatcher._reply(bot, "group", "target", "message", reply)
    buttons = payloads(bot, "group")[0]["keyboard"].content.rows[0].buttons
    assert buttons[0].action.permission.type == 0
    assert buttons[0].action.permission.specify_user_ids == list(users)
    assert buttons[1].action.permission.type == 2
    assert not buttons[1].action.permission.specify_user_ids


@pytest.mark.parametrize("users", [
    "member", None, ("",), ("x" * 129,), ("with space",), ("line\nbreak",),
    ("zero\u200bwidth",), (1,), tuple(str(index) for index in range(21)),
])
async def test_invalid_button_user_ids_keep_reply_content_without_truncation(dispatcher, bot, users):
    reply = CommandReply("翻页内容", keyboard=((CommandButton("下一页", "下一页", user_ids=users),),))
    await dispatcher._reply(bot, "group", "target", "message", reply)
    assert payloads(bot, "group") == [{"group_openid": "target", "msg_id": "message",
                                      "msg_seq": 1, "msg_type": 0, "content": "翻页内容"}]
