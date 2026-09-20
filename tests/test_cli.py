"""Terminal loop and executable acceptance checks with an offline provider."""

import os
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest

from hd2bot.cli import run_cli
from hd2bot.config import PROJECT_ROOT
from hd2bot.router import CommandRouter


def terminal_input(lines):
    inputs = iter(lines)

    def read(prompt):
        assert prompt == "HD2> "
        try:
            return next(inputs)
        except StopIteration:
            raise EOFError from None

    return read


async def test_cli_dispatches_commands_skips_blank_lines_and_handles_eof():
    router = AsyncMock(spec=CommandRouter)
    router.handle.side_effect = ["first response", "second response"]
    output = []
    status = await run_cli(
        router,
        input_func=terminal_input(["", "  ", "战况", "帮助"]),
        output_func=output.append,
    )
    assert status == 0
    assert [call.args[0] for call in router.handle.await_args_list] == ["战况", "帮助"]
    assert output[1:] == ["first response", "second response"]


@pytest.mark.parametrize("exit_command", ["退出", "exit", "/exit", "QUIT", "q"])
async def test_cli_exit_commands_stop_without_dispatch(exit_command):
    router = AsyncMock(spec=CommandRouter)
    output = []
    status = await run_cli(
        router, input_func=terminal_input([exit_command, "战况"]), output_func=output.append
    )
    assert status == 0
    router.handle.assert_not_awaited()
    assert len(output) == 1


async def test_cli_immediate_eof_is_normal_shutdown():
    router = AsyncMock(spec=CommandRouter)
    output = []
    assert await run_cli(
        router, input_func=terminal_input([]), output_func=output.append
    ) == 0
    router.handle.assert_not_awaited()


def mock_process_env(tmp_path):
    env = os.environ.copy()
    env.update({
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "HD2_PROVIDER": "mock",
        "QQ_APP_ID": "",
        "QQ_APP_SECRET": "",
        "LOG_DIR": str(tmp_path / "logs"),
        "DATABASE_PATH": str(tmp_path / "bot.db"),
    })
    return env


def test_real_cli_process_accepts_all_six_commands_without_qq_credentials(tmp_path):
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "run.py"), "--cli", "--provider", "mock"],
        input="战况\n主线\n星球 Meridia\n进攻\n防守\n玩家\nexit\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=PROJECT_ROOT,
        env=mock_process_env(tmp_path),
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.count("HD2> ") == 7
    for expected in ("为了超级地球", "梅里迪亚", "马勒维隆溪", "天使进取", "10000"):
        assert expected in completed.stdout.replace(",", "")
    assert "Traceback" not in completed.stdout + completed.stderr
    assert "暂时处理失败" not in completed.stdout


def test_real_cli_process_eof_exits_cleanly(tmp_path):
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "run.py"), "--cli", "--provider", "mock"],
        input="",
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=PROJECT_ROOT,
        env=mock_process_env(tmp_path),
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "HD2> " in completed.stdout
    assert "Traceback" not in completed.stderr
