import copy
import json
from inspect import signature
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest
from aioresponses import aioresponses
from aioresponses import core as aioresponses_core

from hd2bot.config import Settings
from hd2bot.qq.native_ui import (
    API_BASE,
    SCOPES,
    TOKEN_URL,
    NativeUIClient,
    NativeUIError,
    load_config,
    plan_changes,
    validate_config,
)


@pytest.fixture(autouse=True)
def aioresponses_stream_writer_compatibility(monkeypatch):
    """aiohttp 3.14 added a required argument not yet supplied by aioresponses."""
    if "stream_writer" not in signature(aiohttp.ClientResponse).parameters:
        return
    original = aioresponses_core.ClientResponse

    def response_with_stream_writer(*args, **kwargs):
        kwargs.setdefault("stream_writer", SimpleNamespace(output_size=0))
        return original(*args, **kwargs)

    monkeypatch.setattr(aioresponses_core, "ClientResponse", response_with_stream_writer)


def config():
    return {"schema": 1, "owner": "hd2bot", "menu_mode": "merge",
            "menu": {"items": [{"name": "签到", "type": "send_message", "send_message": "签到"}]},
            "panels": {scope: {"items": [{"type": "command", "name": "签到",
                                        "desc": "每日签到"}]} for scope in SCOPES}}


def snapshot():
    return {"menu": {"menu": None, "version": 0}, "panels": {scope: [] for scope in SCOPES}}


def materialize(desired, remote):
    remote = copy.deepcopy(remote)
    for change in plan_changes(desired, remote):
        if change.path == "/v2/menu":
            remote["menu"] = {"menu": change.body["menu"], "version": 1}
        elif change.method == "POST":
            remote["panels"][change.scope].append({**change.body, "panel_id": "p_" + change.scope})
        else:
            for record in remote["panels"][change.scope]:
                if record["panel"]["remark"] == change.body["panel"]["remark"]:
                    record["panel"] = change.body["panel"]
    return remote


def test_distributable_example_is_blank_and_does_not_change_remote_ui():
    desired = load_config(Path(__file__).resolve().parents[1] / "qq_native_ui.example.json")
    assert desired["panels"] == {} and desired["menu"] is None
    assert plan_changes(desired, snapshot()) == []


def test_first_plan_creates_four_global_panels_then_is_idempotent():
    changes = plan_changes(config(), snapshot())
    assert len(changes) == 5
    assert all(c.body["target_type"] == "all" for c in changes if c.method == "POST")
    assert {c.scope for c in changes if c.method == "POST"} == set(SCOPES)
    assert not plan_changes(config(), materialize(config(), snapshot()))


def test_tencent_generated_menu_icons_do_not_trigger_repeated_put():
    desired = config()
    desired["menu"]["items"].append({"name": "训练", "type": "menu", "sub_menu_items": [
        {"name": "战备英雄", "type": "send_message", "send_message": "战备英雄"},
    ]})
    remote = materialize(desired, snapshot())
    for item in remote["menu"]["menu"]["items"]:
        item["icon"] = "https://bot-resource.example/menu_icon.png"
        for child in item.get("sub_menu_items", []):
            child["icon"] = "https://bot-resource.example/child_icon.png"
    assert plan_changes(desired, remote) == []
    desired["menu"]["items"][0]["send_message"] = "我的等级"
    assert len(plan_changes(desired, remote)) == 1


def test_merge_preserves_unrelated_menu_and_panels():
    remote = snapshot()
    remote["menu"]["menu"] = {"items": [{"name": "客服", "type": "link",
                                         "link": "https://example.com"}]}
    remote["panels"]["group"] = [{"panel_id": "external", "scope": "group",
                                    "target_type": "all", "panel": {"remark": "external",
                                                                      "items": []}}]
    changed = materialize(config(), remote)
    assert changed["menu"]["menu"]["items"][0] == remote["menu"]["menu"]["items"][0]
    assert changed["panels"]["group"][0] == remote["panels"]["group"][0]
    assert not plan_changes(config(), changed)


def test_update_only_owned_panel_and_server_defaults_do_not_trigger_writes():
    remote = materialize(config(), snapshot())
    for records in remote["panels"].values():
        records[0]["panel"]["items"][0]["only_admin"] = False
        records[0]["panel"]["version"] = 7
    assert not plan_changes(config(), remote)
    desired = config()
    desired["panels"]["group"]["items"][0]["name"] = "我的等级"
    changes = plan_changes(desired, remote)
    assert len(changes) == 1
    assert changes[0].method == "PUT"
    assert changes[0].path == "/v2/panels/p_group"


def test_replace_reports_old_menu_and_merge_never_silently_drops_existing_items():
    remote = snapshot()
    remote["menu"]["menu"] = {"items": [{"name": f"旧菜单{i}", "type": "send_message",
                                         "send_message": "旧内容"} for i in range(10)]}
    with pytest.raises(NativeUIError, match="超过 10"):
        plan_changes(config(), remote)
    desired = config()
    desired["menu_mode"] = "replace"
    change = plan_changes(desired, remote)[0]
    assert len(change.before["items"]) == 10
    assert len(change.body["menu"]["items"]) == 1


@pytest.mark.parametrize("mutation", ["long_name", "long_desc", "nested_menu", "http_link",
                                      "many_items", "invalid_scope", "wrong_admin_type"])
def test_invalid_config_rejected_before_network(mutation):
    desired = config()
    item = desired["panels"]["group"]["items"][0]
    if mutation == "long_name":
        item["name"] = "中文" * 4
    elif mutation == "long_desc":
        item["desc"] = "中文" * 8
    elif mutation == "nested_menu":
        desired["menu"]["items"] = [{"name": "菜单", "type": "menu", "sub_menu_items": [
            {"name": "嵌套", "type": "menu", "sub_menu_items": []}]}]
    elif mutation == "http_link":
        item.update(type="link", link="http://example.com")
    elif mutation == "many_items":
        desired["panels"]["group"]["items"] *= 21
    elif mutation == "invalid_scope":
        desired["panels"]["dms"] = {"items": []}
    elif mutation == "wrong_admin_type":
        item["only_admin"] = "false"
    with pytest.raises(NativeUIError):
        validate_config(desired)


def test_ownership_ambiguity_and_total_limit_abort_whole_plan():
    remote = materialize(config(), snapshot())
    remote["panels"]["group"] *= 2
    with pytest.raises(NativeUIError, match="重复"):
        plan_changes(config(), remote)
    remote = materialize(config(), snapshot())
    remote["panels"]["group"][0]["target_type"] = "specific"
    with pytest.raises(NativeUIError, match="全局"):
        plan_changes(config(), remote)
    remote = snapshot()
    remote["panels"]["group"] = [{"panel": {"remark": "other"}} for _ in range(20)]
    with pytest.raises(NativeUIError, match="20 个"):
        plan_changes(config(), remote)


def settings():
    return Settings(qq_app_id="test-app", qq_app_secret="DO_NOT_SHOW_SECRET", qq_sandbox=False)


async def test_snapshot_paginates_all_scopes_and_uses_token_only_in_headers():
    with aioresponses() as mocked:
        mocked.post(TOKEN_URL, payload={"access_token": "DO_NOT_SHOW_TOKEN", "expires_in": 7200})
        mocked.get(API_BASE + "/v2/menu", payload={"menu": None, "version": 0})
        for scope in SCOPES:
            mocked.get(API_BASE + f"/v2/panels?scope={scope}&limit=50", payload={
                "records": [{"panel_id": "first"}], "next_cursor": "two", "is_end": False})
            mocked.get(API_BASE + f"/v2/panels?scope={scope}&limit=50&cursor=two", payload={
                "records": [{"panel_id": "second"}], "next_cursor": "", "is_end": True})
        async with NativeUIClient(settings()) as client:
            result = await client.snapshot()
        assert all(len(records) == 2 for records in result["panels"].values())
        assert "DO_NOT_SHOW" not in json.dumps(result)
        assert len(mocked.requests) == 10
        for (method, url), calls in mocked.requests.items():
            assert method == "GET" or str(url) == TOKEN_URL
            if method == "GET":
                assert calls[0].kwargs["headers"]["Authorization"] == "QQBot DO_NOT_SHOW_TOKEN"


@pytest.mark.parametrize("status,body", [
    (200, {"code": 100016, "message": "DO_NOT_SHOW_SECRET"}),
    (200, {"err_code": 11253, "message": "DO_NOT_SHOW_SECRET"}),
    (403, {"message": "DO_NOT_SHOW_SECRET"}),
])
async def test_http_200_business_failures_are_not_success_and_do_not_echo_response(status, body):
    with aioresponses() as mocked:
        mocked.post(TOKEN_URL, status=status, payload=body)
        async with NativeUIClient(settings()) as client:
            with pytest.raises(NativeUIError) as error:
                await client.snapshot()
        assert "DO_NOT_SHOW" not in str(error.value)
        assert len(mocked.requests) == 1


async def test_partial_apply_failure_does_not_retry_post_or_delete_any_panel():
    changes = plan_changes(config(), snapshot())
    with aioresponses() as mocked:
        mocked.post(TOKEN_URL, payload={"access_token": "test-token", "expires_in": 7200})
        mocked.put(API_BASE + "/v2/menu", payload={"version": 1})
        mocked.post(API_BASE + "/v2/panels", exception=aiohttp.ClientConnectionError("test"))
        async with NativeUIClient(settings()) as client:
            with pytest.raises(NativeUIError, match="重新查询"):
                await client.apply(changes)
        posts = [calls for (method, url), calls in mocked.requests.items()
                 if method == "POST" and str(url).endswith("/v2/panels")]
        assert len(posts) == 1 and len(posts[0]) == 1
        assert all(method != "DELETE" for method, _ in mocked.requests)


async def test_repeated_cursor_aborts_instead_of_looping():
    with aioresponses() as mocked:
        mocked.post(TOKEN_URL, payload={"access_token": "test-token", "expires_in": 7200})
        mocked.get(API_BASE + "/v2/menu", payload={"menu": None})
        mocked.get(API_BASE + "/v2/panels?scope=c2c&limit=50",
                   payload={"records": [], "next_cursor": "repeat", "is_end": False})
        mocked.get(API_BASE + "/v2/panels?scope=c2c&limit=50&cursor=repeat",
                   payload={"records": [], "next_cursor": "repeat", "is_end": False})
        async with NativeUIClient(settings()) as client:
            with pytest.raises(NativeUIError, match="分页游标"):
                await client.snapshot()


async def test_request_rejects_unrelated_endpoints_without_getting_token():
    async with NativeUIClient(settings()) as client:
        with pytest.raises(NativeUIError, match="只允许"):
            await client.request("POST", "/v2/groups/group-id/messages", body={"content": "x"})
