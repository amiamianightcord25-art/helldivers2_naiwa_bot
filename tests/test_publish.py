import json
import runpy
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKS = runpy.run_path(str(ROOT / "scripts/check_publish.py"))


@pytest.mark.parametrize("name, content", [
    (".env.example", b'QQ_APP_ID="12345678"'),
    (".env.example", b'export HD2_SUPER_CONTACT=operator@example.invalid'),
    ("helper_download.example.json", b'{"url":"https://example.invalid/private"}'),
    ("group_push.example.json", b'{"manager_user_ids":["example-user"]}'),
    ("group_push.example.json", b'{"enabled":true}'),
    ("menu.example.json", b'{"pages":{"main":{"title":"operator menu"}}}'),
    ("release_notice.example.json", b'{"body":"operator announcement"}'),
    ("career_ws.example.json", b'{"nested":{"token":"example-test-value"}}'),
])
def test_publication_rejects_populated_operator_templates(name, content):
    assert not CHECKS["blank_example"](name, content)


def test_all_shipped_templates_have_empty_operator_fields():
    examples = [ROOT / ".env.example", *ROOT.glob("*.example.json")]
    for example in examples:
        assert CHECKS["blank_example"](example.name, example.read_bytes()), example.name


@pytest.mark.parametrize("prefix", ["I:" + "/", "C:" + "\\", "/home" + "/example/"])
def test_research_and_source_cannot_publish_workstation_paths(prefix):
    for name in ("research/notes.md", "scripts/example.py"):
        assert CHECKS["workstation_path"](name, (prefix + "project/file").encode())


def test_public_urls_and_generic_deployment_paths_are_allowed():
    assert not CHECKS["workstation_path"](
        "research/notes.md", b"https://example.invalid/docs /opt/hd2bot /var/lib/hd2bot",
    )


def test_removed_workstation_paths_still_block_history(tmp_path, monkeypatch):
    scan = CHECKS["history_problems"]
    monkeypatch.setitem(scan.__globals__, "ROOT", tmp_path)

    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    git("init", "-b", "main")
    note = tmp_path / "notes.md"
    note.write_text("I:" + "\\" + "example-project/file", encoding="utf-8")
    git("add", "notes.md")
    identity = ("-c", "user.name=Example", "-c", "user.email=example@example.invalid")
    git(*identity, "commit", "-m", "Add example")
    note.write_text("Generic documentation", encoding="utf-8")
    git("add", "notes.md")
    git(*identity, "commit", "-m", "Replace example")
    problems = scan(set())
    assert any(label.endswith(":notes.md") and reason == "absolute workstation path"
               for label, reason in problems)


def test_actual_operator_ids_and_nested_configuration_are_checked(tmp_path, monkeypatch):
    check = CHECKS["known_local_values"]
    monkeypatch.setitem(check.__globals__, "ROOT", tmp_path)
    (tmp_path / ".env").write_text('QQ_APP_ID="12345678"\nHD2_SUPER_CONTACT=example@example.com')
    (tmp_path / "data").mkdir()
    (tmp_path / "data/group_push.json").write_text('{"manager_user_ids":["synthetic-user-id"]}')
    (tmp_path / "data/service.private.json").write_text(
        '{"service":{"token":"synthetic-test-token","url":"wss://example.invalid/ws"}}',
    )
    (tmp_path / "data/deployment_receipt.private.json").write_text(
        '{"ssh_host":"shared-project-name"}',
    )
    values = check()
    assert {b"12345678", b"synthetic-user-id", b"synthetic-test-token",
            b"wss://example.invalid/ws"} <= values
    assert b"example@example.com" not in values
    assert b"shared-project-name" not in values


def test_empty_env_template_loads_without_private_files(tmp_path, monkeypatch):
    from dotenv import dotenv_values

    from hd2bot.config import Settings

    for key in dotenv_values(ROOT / ".env.example"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_bytes((ROOT / ".env.example").read_bytes())
    settings = Settings.load(tmp_path)
    assert settings.qq_app_id == settings.qq_app_secret == settings.qq_push_user == ""
    assert settings.career_config_path is None
    assert settings.database_path == tmp_path / "data/bot.db"


def test_empty_group_template_is_disabled_and_preserves_usable_default_limits(tmp_path):
    from hd2bot.services.group_push import GroupPushAccess

    path = tmp_path / "group_push.json"
    path.write_text(json.dumps(json.loads((ROOT / "group_push.example.json").read_bytes())))
    policy = GroupPushAccess(None, None, path, "example-app").policy()
    assert policy["enabled"] is False
    assert policy["allowed_groups"] == policy["manager_user_ids"] == []
    assert policy["default_daily_limit"] > 0
    assert policy["default_min_gap_minutes"] > 0
