import json
from unittest.mock import AsyncMock

import pytest

from hd2bot.career.onboarding import CareerOnboarding
from hd2bot.commands.parser import parse_command
from hd2bot.presentation import ChatContext
from hd2bot.router import CommandRouter


@pytest.mark.parametrize("command", ["获取战绩", "/获取战绩", "下载助手", "同步战绩", "bind"])
async def test_download_step_does_not_issue_or_replace_challenge(tmp_path, command):
    config = tmp_path / "download.json"
    config.write_text(
        json.dumps({"url": "https://example.invalid/test-package", "password": "1234"})
    )
    service = AsyncMock()
    router = CommandRouter(None, service, onboarding=CareerOnboarding(config))
    reply = await router.respond(command, context=ChatContext("c2c", "u", "u"))
    assert (
        "https://example.invalid/test-package" in reply.text and "提取密码：1234" in reply.text
    )
    assert "全部解压" in reply.text and "助手已经打开后" in reply.text
    assert "不会提前开始" in reply.text
    assert "每次需要获取新的战绩数据" in reply.text
    assert "旧凭据不能复用" in reply.text
    service.challenge.assert_not_awaited()
    service.bind.assert_not_awaited()
    service.query_user.assert_not_awaited()


@pytest.mark.parametrize(
    "command", ["获取验证码", "/获取验证码", "领取验证码", "准备好了", "已打开助手"]
)
async def test_ready_step_issues_one_challenge_and_explains_submission(command):
    service = AsyncMock()
    service.challenge.return_value = {"challenge": "AB3K9M"}
    reply = await CommandRouter(None, service).respond(
        command, context=ChatContext("c2c", "u", "u")
    )
    service.challenge.assert_awaited_once_with("qq:u")
    assert "AB3K9M" in reply.text and "120 秒从现在开始" in reply.text
    assert "生成同步胶囊" in reply.text and "当前私聊" in reply.text


async def test_group_guidance_never_issues_challenge():
    service = AsyncMock()
    router = CommandRouter(None, service)
    for command in ("获取战绩", "获取验证码", "下载助手", "同步帮助"):
        reply = await router.respond(command, context=ChatContext("group", "g", "u"))
        assert "私聊" in reply.text
    service.challenge.assert_not_awaited()


def test_download_configuration_hot_reload_and_missing_link(tmp_path):
    path = tmp_path / "download.json"
    guide = CareerOnboarding(path)
    assert "尚未发布" in guide.guide() and "http" not in guide.guide()
    path.write_text(json.dumps({"url": "https://example.invalid/new", "password": ""}))
    assert "https://example.invalid/new" in guide.guide() and "提取密码：无" in guide.guide()
    path.write_text(json.dumps({"url": "https://user:secret@example.com/file", "password": "x"}))
    assert "secret" not in guide.guide() and "尚未发布" in guide.guide()


def test_guide_distinguishes_snapshot_reads_from_fresh_sync(tmp_path):
    guide = CareerOnboarding(tmp_path / "download.json")
    assert "查看保存的快照，不需要再次获取凭据" in guide.guide()
    assert "每次获取新数据都要重新领取验证码" in guide.troubleshooting()


async def test_troubleshooting_works_without_query_backend():
    reply = await CommandRouter(None).respond("同步帮助", context=ChatContext("dms", "s", "u"))
    assert "全部解压" in reply.text and "同一个" in reply.text
    assert "获取验证码" in reply.text


def test_helpful_aliases_do_not_change_readonly_career_query():
    for text in ("战绩", "查询战绩", "查战绩"):
        assert parse_command(text).name == "战绩"
    assert parse_command("获取战绩").name == "获取战绩"
    assert parse_command("已打开助手").name == "获取验证码"


async def test_download_instructions_available_without_backend_configuration(tmp_path):
    reply = await CommandRouter(
        None, onboarding=CareerOnboarding(tmp_path / "not-yet.json")
    ).respond("获取战绩")
    assert "3 步完成" in reply.text and "获取验证码" in reply.text
    assert "服务尚未配置" not in reply.text
