"""Settings 解析层单测（纯函数为主）。

不碰真实 .env 文件：每个测试用 monkeypatch 注入 env 变量，显式禁用文件加载。
"""

from __future__ import annotations

import os

import pytest

from chat_bot.config import Settings


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """清掉所有可能干扰的 env，并禁用文件加载。

    这样每个测试跑在完全空白的环境里，不受宿主机 .env 影响。
    """
    # 先清，每个测试再显式塞自己要的
    for key in list(os.environ):
        if key.startswith(
            (
                "DISCORD_",
                "INTERNAL_",
                "GMAIL_",
                "DIGEST_",
                "CHATBOT_",
                "IH_",
            )
        ):
            monkeypatch.delenv(key, raising=False)
    # 把 env file 指到不存在的路径，model_config.env_file 会识别为空
    monkeypatch.setenv("CHATBOT_ENV_FILE", "/dev/null/nope")


def _settings(**env: str) -> Settings:
    """用指定 env 实例化 Settings。

    显式传 `_env_file=None` 绕开 model_config 在 import 时已经捕获的真实 .env 路径，
    只让 Settings 从我们刚 setenv 的环境变量读。
    """
    for k, v in env.items():
        os.environ[k] = v
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_watch_channel_ids_empty():
    s = _settings(DISCORD_BOT_TOKEN="t", INTERNAL_API_KEY="k")
    assert s.watch_channel_ids == set()


def test_watch_channel_ids_single():
    s = _settings(
        DISCORD_BOT_TOKEN="t", INTERNAL_API_KEY="k", DISCORD_WATCH_CHANNEL_IDS="123"
    )
    assert s.watch_channel_ids == {123}


def test_watch_channel_ids_multi_with_spaces():
    # 用户手写 env 经常带空格 / 尾逗号，容错这些是防止"bot 静默不响应"的关键
    s = _settings(
        DISCORD_BOT_TOKEN="t",
        INTERNAL_API_KEY="k",
        DISCORD_WATCH_CHANNEL_IDS=" 111 , 222,333 ,",
    )
    assert s.watch_channel_ids == {111, 222, 333}


def test_discord_guild_id_empty_string_becomes_none():
    # 真实场景：.env 里写 DISCORD_GUILD_ID= 空值时，pydantic 默认会 int 解析失败
    # field_validator 要能兜住这种情况，不能让 bot 启动就崩
    s = _settings(
        DISCORD_BOT_TOKEN="t", INTERNAL_API_KEY="k", DISCORD_GUILD_ID=""
    )
    assert s.discord_guild_id is None


def test_discord_guild_id_parsed():
    s = _settings(
        DISCORD_BOT_TOKEN="t", INTERNAL_API_KEY="k", DISCORD_GUILD_ID="42"
    )
    assert s.discord_guild_id == 42


def test_internal_submit_url_default_base():
    s = _settings(DISCORD_BOT_TOKEN="t", INTERNAL_API_KEY="k")
    assert s.internal_submit_url == "http://127.0.0.1:8080/api/community/links/internal"


def test_internal_submit_url_strips_trailing_slash():
    # 用户塞了带斜杠的 base URL 也不该挂
    s = _settings(
        DISCORD_BOT_TOKEN="t",
        INTERNAL_API_KEY="k",
        IH_BACKEND_URL="http://example.com:9999/",
    )
    assert (
        s.internal_submit_url
        == "http://example.com:9999/api/community/links/internal"
    )


def test_email_configured_all_missing():
    s = _settings(DISCORD_BOT_TOKEN="t", INTERNAL_API_KEY="k")
    assert s.email_configured is False


def test_email_configured_partial():
    # 只填一半不算数，digest 需要三项齐全才发邮件
    s = _settings(
        DISCORD_BOT_TOKEN="t",
        INTERNAL_API_KEY="k",
        GMAIL_USER="a@b.com",
    )
    assert s.email_configured is False


def test_email_configured_all_present():
    s = _settings(
        DISCORD_BOT_TOKEN="t",
        INTERNAL_API_KEY="k",
        GMAIL_USER="a@b.com",
        GMAIL_APP_PASSWORD="xxxx yyyy zzzz wwww",
        DIGEST_EMAIL_TO="a@b.com,c@d.com",
    )
    assert s.email_configured is True
