import base64
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from nonebot.adapters.qq.models import Dispatch

from hd2bot.config import Settings
from hd2bot.presentation import CommandReply
from hd2bot.qq.adapter import QQAdapter, QQDispatcher
from hd2bot.qq.welcome import GROUP_WELCOME, PRIVATE_WELCOME, WelcomeStore
from hd2bot.rendering import HtmlRenderer, QueryCard
from hd2bot.rendering.models import StarMap


def added(scope, event_id="added-event", *, age=0):
    data = {"timestamp": (datetime.now(timezone.utc)-timedelta(seconds=age)).isoformat()}
    if scope == "group":
        data.update(group_openid="synthetic-group", op_member_openid="synthetic-user")
    else:
        data["openid"] = "synthetic-user"
    return QQAdapter.payload_to_event(Dispatch.model_validate({
        "op": 0, "s": 1, "id": event_id,
        "t": "GROUP_ADD_ROBOT" if scope == "group" else "FRIEND_ADD", "d": data,
    }))


@pytest.mark.parametrize("scope", ["group", "c2c"])
async def test_added_event_sends_welcome_with_event_id_and_survives_restart(tmp_path, scope):
    store = WelcomeStore(tmp_path/"welcomes.db", "app-one")
    bot = AsyncMock()
    dispatcher = QQDispatcher(Settings(), AsyncMock(), welcome_store=store)
    message = added(scope)
    await dispatcher.welcome(message, bot)
    send = bot.post_group_messages if scope == "group" else bot.post_c2c_messages
    assert send.await_count == 1
    request = send.await_args.kwargs
    assert request["event_id"] == "added-event" and "msg_id" not in request
    assert request["content"] == (GROUP_WELCOME if scope == "group" else PRIVATE_WELCOME)
    again = QQDispatcher(Settings(), AsyncMock(), welcome_store=WelcomeStore(tmp_path/"welcomes.db", "app-one"))
    await again.welcome(message, bot)
    assert send.await_count == 1
    await again.welcome(added(scope, "new-add-event"), bot)
    assert send.await_count == 2
    assert "synthetic-user" not in (tmp_path/"welcomes.db").read_bytes().decode("latin1")


async def test_stale_event_and_missing_event_id_do_not_send(tmp_path):
    dispatcher = QQDispatcher(Settings(), AsyncMock(), welcome_store=WelcomeStore(tmp_path/"w.db", "app"))
    bot = AsyncMock()
    await dispatcher.welcome(added("group", age=301), bot)
    await dispatcher.welcome(added("group", event_id=None), bot)
    bot.post_group_messages.assert_not_awaited()


async def test_send_failure_is_not_retried_as_spam(tmp_path):
    dispatcher = QQDispatcher(Settings(), AsyncMock(), welcome_store=WelcomeStore(tmp_path/"w.db", "app"))
    bot = AsyncMock()
    bot.post_c2c_messages.side_effect = RuntimeError("ambiguous send failure")
    await dispatcher.welcome(added("c2c"), bot)
    await dispatcher.welcome(added("c2c"), bot)
    assert bot.post_c2c_messages.await_count == 1


@pytest.mark.parametrize("scope", ["c2c", "dms"])
async def test_first_private_message_gets_onboarding_once(tmp_path, scope):
    from test_channel_dm import event
    from test_qq import make_event

    router, bot = AsyncMock(), AsyncMock()
    router.respond.return_value = CommandReply("正常查询结果")
    dispatcher = QQDispatcher(Settings(image_enabled=False), router,
                              welcome_store=WelcomeStore(tmp_path/"w.db", "app"))
    first = event() if scope == "dms" else make_event()
    second = event(message_id="second") if scope == "dms" else make_event(message_id="second")
    send = bot.post_dms_messages if scope == "dms" else bot.post_c2c_messages
    await dispatcher.process(first, bot)
    combined = "".join(c.kwargs.get("content", "") for c in send.await_args_list)
    assert PRIVATE_WELCOME in combined and "正常查询结果" in combined
    send.reset_mock()
    await dispatcher.process(second, bot)
    assert send.await_args.kwargs["content"] == "正常查询结果"


async def test_friend_added_then_private_query_does_not_repeat_welcome(tmp_path):
    from test_qq import make_event

    store = WelcomeStore(tmp_path/"w.db", "app")
    # Match the message test fixture's recipient.
    await store.claim_added_event("c2c", "user-local", "add-event")
    router, bot = AsyncMock(), AsyncMock()
    router.respond.return_value = CommandReply("结果")
    dispatcher = QQDispatcher(Settings(image_enabled=False), router, welcome_store=store)
    await dispatcher.process(make_event(), bot)
    assert bot.post_c2c_messages.await_args.kwargs["content"] == "结果"


async def test_app_ids_do_not_share_onboarding_history(tmp_path):
    first, second = WelcomeStore(tmp_path/"w.db", "one"), WelcomeStore(tmp_path/"w.db", "two")
    assert await first.claim_first_message("c2c", "u")
    assert not await first.claim_first_message("c2c", "u")
    assert await second.claim_first_message("c2c", "u")


@pytest.mark.parametrize("width", [720, 1200, 1800])
@pytest.mark.parametrize("star_map", [False, True])
def test_all_card_templates_embed_exact_original_qr_with_caption(tmp_path, width, star_map):
    from PIL import Image

    expected = tmp_path / "synthetic-qr.png"
    Image.new("RGB", (32, 32), "white").save(expected)
    card = QueryCard("测试图", star_map=StarMap((), ()) if star_map else None)
    html = HtmlRenderer(width=width, qr_path=expected).render_html(card)
    found = re.search(r'<img class="experience-qr" src="data:image/png;base64,([^\"]+)"', html)
    assert found
    assert base64.b64decode(found[1]) == expected.read_bytes()
    assert html.count("QQ扫一扫体验") == 1
    assert "align-items: flex-end" in html and "padding: 12px; background: #fff" in html


def test_blank_distribution_does_not_require_an_operator_qr(tmp_path):
    html = HtmlRenderer(qr_path=tmp_path / "not-configured.png").render_html(QueryCard("测试"))
    assert 'class="experience-qr"' not in html
    assert "QQ扫一扫体验" not in html


async def test_welcome_storage_failure_does_not_break_normal_reply():
    from test_qq import make_event

    router, bot, store = AsyncMock(), AsyncMock(), AsyncMock()
    router.respond.return_value = CommandReply("查询仍然成功")
    store.claim_first_message.side_effect = OSError("local write unavailable")
    dispatcher = QQDispatcher(Settings(image_enabled=False), router, welcome_store=store)
    await dispatcher.process(make_event(), bot)
    assert bot.post_c2c_messages.await_args.kwargs["content"] == "查询仍然成功"


async def test_concurrent_first_messages_claim_once(tmp_path):
    import asyncio

    store = WelcomeStore(tmp_path/"w.db", "app")
    values = await asyncio.gather(*(store.claim_first_message("c2c", "u") for _ in range(8)))
    assert values.count(True) == 1
