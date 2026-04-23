"""配置加载。

复用 involution-hell 后端的 .env 文件（/home/ubuntu/involution-hell-project/backend/.env），
避免多地维护密码。

ChatBot 自己只读两组变量：
1. DISCORD_*：Bot token / 监听频道 / Guild ID
2. 后端 API 对接：IH_BACKEND_URL（127.0.0.1:8080）+ INTERNAL_API_KEY（共享密钥）
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_ENV = "/home/ubuntu/involution-hell-project/backend/.env"
ENV_FILE = os.environ.get("CHATBOT_ENV_FILE", _DEFAULT_ENV)


class Settings(BaseSettings):
    """运行时配置。Bot 不直连数据库，只调后端 API。"""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE if Path(ENV_FILE).exists() else None,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- Discord ----------
    discord_bot_token: SecretStr = Field(..., alias="DISCORD_BOT_TOKEN")
    discord_watch_channel_ids: str = Field("", alias="DISCORD_WATCH_CHANNEL_IDS")
    discord_guild_id: int | None = Field(None, alias="DISCORD_GUILD_ID")

    # ---------- 后端 API 对接 ----------
    ih_backend_url: str = Field("http://127.0.0.1:8080", alias="IH_BACKEND_URL")
    internal_api_key: SecretStr = Field(..., alias="INTERNAL_API_KEY")
    chatbot_api_timeout: float = Field(15.0, alias="CHATBOT_API_TIMEOUT")

    # ---------- 审核摘要 digest ----------
    # Discord 管理员频道（空则不推 Discord，只发邮件）
    discord_admin_channel_id: int | None = Field(None, alias="DISCORD_ADMIN_CHANNEL_ID")
    # 每日推送时刻（Asia/Shanghai），24h 制 "HH:MM"
    digest_time_cst: str = Field("09:00", alias="DIGEST_TIME_CST")

    # ---------- FLAGGED 实时 alert ----------
    # 后端 webhook → Bot 内嵌 aiohttp server 接收端口，只绑 127.0.0.1
    chatbot_alert_port: int = Field(6200, alias="CHATBOT_ALERT_PORT")

    # ---------- Gmail SMTP ----------
    # 未填时不发邮件（但 Discord 推送仍走）
    gmail_user: str = Field("", alias="GMAIL_USER")
    gmail_app_password: SecretStr = Field(SecretStr(""), alias="GMAIL_APP_PASSWORD")
    digest_email_to: str = Field("", alias="DIGEST_EMAIL_TO")

    @field_validator("discord_guild_id", "discord_admin_channel_id", mode="before")
    @classmethod
    def _empty_to_none(cls, v: object) -> object:
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    @property
    def email_configured(self) -> bool:
        """三项齐全才算配好邮件。"""
        return (
            bool(self.gmail_user)
            and bool(self.gmail_app_password.get_secret_value())
            and bool(self.digest_email_to)
        )

    @property
    def watch_channel_ids(self) -> set[int]:
        raw = self.discord_watch_channel_ids.strip()
        if not raw:
            return set()
        return {int(x) for x in raw.split(",") if x.strip()}

    @property
    def internal_submit_url(self) -> str:
        """拼出 /api/community/links/internal 的完整 URL。"""
        return self.ih_backend_url.rstrip("/") + "/api/community/links/internal"


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
