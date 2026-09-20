import logging

import pytest

from hd2bot.config import Settings
from hd2bot.logging_setup import SensitiveFilter


def test_relative_paths_follow_project(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", "data/custom.db")
    settings = Settings.load(tmp_path)
    assert settings.database_path == tmp_path / "data/custom.db"
    assert settings.qq_app_secret not in repr(settings) or not settings.qq_app_secret


def test_log_secrets_are_redacted():
    record = logging.LogRecord("test", 20, "", 0, "Authorization: Bearer sample-test-value", (), None)
    SensitiveFilter().filter(record)
    assert "sample-test-value" not in record.getMessage()


@pytest.mark.parametrize("message", [
    "QQBot synthetic-dynamic-value",
    '{"access_token": "synthetic-dynamic-value"}',
    '{"clientSecret": "synthetic-dynamic-value"}',
    '{"token": "synthetic-dynamic-value"}',
    "cookie=synthetic-dynamic-value",
    "Authorization: Bearer synthetic-dynamic-value",
])
def test_log_auth_schemes_and_fields_are_redacted(message):
    record = logging.LogRecord("sdk", 30, "", 0, message, (), None)
    SensitiveFilter().filter(record)
    assert "synthetic-dynamic-value" not in record.getMessage()


def test_invalid_sandbox_flag_does_not_select_production(tmp_path, monkeypatch):
    monkeypatch.setenv("QQ_SANDBOX", "ture")
    with pytest.raises(ValueError):
        Settings.load(tmp_path)


@pytest.mark.parametrize("value", ["0", "5", "-1"])
def test_render_concurrency_rejects_unbounded_resource_settings(tmp_path, monkeypatch, value):
    monkeypatch.setenv("HD2_RENDER_CONCURRENCY", value)
    with pytest.raises(ValueError, match="HD2_RENDER_CONCURRENCY"):
        Settings.load(tmp_path)


def test_server_can_limit_rendering_to_one_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("HD2_RENDER_CONCURRENCY", "1")
    assert Settings.load(tmp_path).render_concurrency == 1


def test_empty_distributable_settings_use_builtin_defaults(tmp_path, monkeypatch):
    for name in ("HD2_RENDER_CONCURRENCY", "HD2_TIMEOUT", "QQ_SANDBOX", "HD2_PROVIDER"):
        monkeypatch.setenv(name, "")
    settings = Settings.load(tmp_path)
    assert settings.render_concurrency == 2 and settings.timeout == 12
    assert settings.qq_sandbox is True and settings.provider == "auto"
