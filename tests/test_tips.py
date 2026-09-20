import copy
import json

import pytest

from hd2bot.hd2.errors import CommandError
from hd2bot.presentation import ChatContext, CommandButton
from hd2bot.services.tips import TipsService, load_tips


class FirstChoice:
    def choice(self, choices):
        return choices[0]


class Clock:
    value = 0

    def __call__(self):
        return self.value


@pytest.fixture
def catalogue():
    return {"schema": 1, "collected_at": "2026-09-20", "items": [
        {"id": f"tip-{number}", "text": f"测试加载小贴士 {number}。",
         "english": f"Fixture loading tip {number}.",
         "source_url": "https://example.com/Loading_Screen_Tips",
         "translation_status": "reference_translation"}
        for number in range(1, 4)
    ]}


def write_catalogue(tmp_path, value):
    path = tmp_path / "tips.json"
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def service(tmp_path, catalogue):
    return TipsService(write_catalogue(tmp_path, catalogue), rng=FirstChoice())


def test_tip_reply_has_reference_translation_source_and_button(service):
    result = service.reply("1")
    assert "1/3" in result.text
    assert "测试加载小贴士 1。" in result.text
    assert "中文为参考翻译" in result.text
    assert "尚未核实为游戏内官方简体原文" in service.reply("来源").text
    assert "小贴士 来源" in result.text
    assert "https://" not in result.text
    assert result.card is None
    assert result.keyboard == ((CommandButton("再来一条", "小贴士"),),)


def test_source_reply_distinguishes_verified_and_reference(tmp_path, catalogue):
    catalogue["items"][0]["translation_status"] = "verified_zh_cn"
    service = TipsService(write_catalogue(tmp_path, catalogue))
    result = service.reply("来源")
    assert "共 3 条" in result.text
    assert "其中 2 条中文为参考翻译" in result.text
    assert "其中 1 条标注为已核对" in result.text
    assert result.text.count("https://example.com/Loading_Screen_Tips") == 1
    assert "中文已核对游戏内简体原文" in service.reply("1").text
    assert "参考翻译" not in service.reply("1").text


@pytest.mark.parametrize("argument", ["1", "01", "＃１", "１", "tip-1", "TIP-1"])
def test_numeric_and_stable_id_lookup(service, argument):
    assert "测试加载小贴士 1。" in service.reply(argument).text


@pytest.mark.parametrize("argument", ["0", "4", "9999"])
def test_out_of_range_has_current_bounds(service, argument):
    with pytest.raises(CommandError, match="1～3"):
        service.reply(argument)


@pytest.mark.parametrize("argument", ["-1", "1.5", "一", "tip-missing", "a" * 65, None])
def test_bad_argument_gives_usage_without_mutating_history(service, argument):
    with pytest.raises(CommandError, match="用法"):
        service.reply(argument)
    assert not service._recent


@pytest.mark.parametrize("scope", ["group", "channel", "c2c", "dms"])
def test_each_scope_and_cli_avoid_immediate_repeat(service, scope):
    context = ChatContext(scope, "conversation-a", "user-a")
    results = [service.reply(context=context).text for _ in range(10)]
    assert all(first != second for first, second in zip(results, results[1:]))
    assert service.reply().text != service.reply().text


@pytest.mark.parametrize("scope", ["group", "channel"])
def test_members_share_last_tip_in_public_conversation(service, scope):
    first = service.reply(context=ChatContext(scope, "target", "user-a"))
    second = service.reply(context=ChatContext(scope, "target", "user-b"))
    assert first.text != second.text


@pytest.mark.parametrize("scope", ["c2c", "dms"])
def test_private_users_with_same_target_have_separate_history(service, scope):
    first = service.reply(context=ChatContext(scope, "target", "user-a"))
    second = service.reply(context=ChatContext(scope, "target", "user-b"))
    assert first.text == second.text


def test_scope_and_target_isolation(service):
    contexts = [ChatContext("group", "target-a", "user-a"),
                ChatContext("group", "target-b", "user-a"),
                ChatContext("channel", "target-a", "user-a"), None]
    assert len({service.reply(context=context).text for context in contexts}) == 1


def test_specified_tip_is_excluded_from_next_random_tip(service):
    assert service.reply("1").text != service.reply().text


def test_source_view_does_not_consume_random_choice_or_history(service):
    first = service.reply()
    history = dict(service._recent)
    service.reply("来源")
    assert dict(service._recent) == history
    assert service.reply().text != first.text


def test_single_item_catalogue_still_works(tmp_path, catalogue):
    catalogue["items"] = catalogue["items"][:1]
    service = TipsService(write_catalogue(tmp_path, catalogue))
    assert service.reply().text == service.reply().text


def test_history_expires_and_lru_capacity_is_bounded(tmp_path, catalogue):
    clock = Clock()
    service = TipsService(write_catalogue(tmp_path, catalogue), rng=FirstChoice(),
                          clock=clock, session_ttl=10, max_sessions=2)
    a, b, c = (ChatContext("group", target) for target in "abc")
    first = service.reply(context=a)
    clock.value = 1
    service.reply(context=b)
    clock.value = 2
    service.reply(context=a)
    clock.value = 3
    service.reply(context=c)
    assert len(service._recent) == 2
    assert ("group", "b", "") not in service._recent
    clock.value = 13
    assert service.reply(context=a).text == first.text
    assert len(service._recent) == 1


@pytest.mark.parametrize("changes", [
    {"schema": True}, {"schema": 2}, {"schema": "1"}, {"unexpected": 1},
    {"items": []}, {"items": {}}, {"items": [None]},
    {"collected_at": "yesterday"}, {"collected_at": "2026-02-30"},
])
def test_invalid_top_level_schema_rejected(tmp_path, catalogue, changes):
    catalogue.update(changes)
    with pytest.raises(ValueError):
        load_tips(write_catalogue(tmp_path, catalogue))


@pytest.mark.parametrize("field,value", [
    ("id", ""), ("id", "bad id"), ("id", 1),
    ("text", "English only"), ("text", " 中文空格 "), ("text", "中文\n换行"),
    ("text", "中" * 601), ("english", ""), ("english", 12),
    ("source_url", "javascript:alert(1)"), ("source_url", "https:///missing"),
    ("source_url", "https://example.com/a b"), ("source_url", "https://u:p@example.com/"),
    ("translation_status", "official"), ("translation_status", []),
])
def test_invalid_entries_rejected(tmp_path, catalogue, field, value):
    catalogue["items"][0][field] = value
    with pytest.raises(ValueError):
        load_tips(write_catalogue(tmp_path, catalogue))


def test_duplicate_ids_missing_and_extra_fields_rejected(tmp_path, catalogue):
    invalids = [copy.deepcopy(catalogue) for _ in range(3)]
    invalids[0]["items"][1]["id"] = "tip-1"
    del invalids[1]["items"][0]["english"]
    invalids[2]["items"][0]["invented"] = True
    for invalid in invalids:
        with pytest.raises(ValueError):
            load_tips(write_catalogue(tmp_path, invalid))


def test_duplicate_json_keys_and_oversize_file_rejected(tmp_path):
    path = tmp_path / "tips.json"
    path.write_text('{"schema":1,"schema":1,"items":[]}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_tips(path)
    path.write_bytes(b" " * 1_048_577)
    with pytest.raises(ValueError, match="too large"):
        load_tips(path)


def test_source_attribution_metadata_is_supported(tmp_path, catalogue):
    catalogue.update({
        "sources": [{"name": "Helldivers Wiki", "url": "https://example.com/tips",
                     "retrieved_at": "2026-09-20", "revision": "12345"}],
        "attribution": "Helldivers Wiki contributors; Chinese reference translation by bot project.",
        "license": "CC BY-SA 4.0",
    })
    assert len(load_tips(write_catalogue(tmp_path, catalogue))) == 3
    catalogue["sources"][0]["retrieved_at"] = "invalid"
    with pytest.raises(ValueError):
        load_tips(write_catalogue(tmp_path, catalogue))


@pytest.mark.parametrize("kwargs", [
    {"max_sessions": 0}, {"max_sessions": True}, {"max_sessions": 1.5},
    {"session_ttl": 0}, {"session_ttl": True}, {"session_ttl": float("inf")},
    {"session_ttl": float("nan")},
])
def test_invalid_history_limits_rejected(tmp_path, catalogue, kwargs):
    with pytest.raises(ValueError):
        TipsService(write_catalogue(tmp_path, catalogue), **kwargs)
