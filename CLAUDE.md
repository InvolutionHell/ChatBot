# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                          # install deps (Python 3.12, managed by uv)
uv run chat-bot                  # run the bot locally
uv run mcp-server                # run the MCP server (stdio transport)
uv run pytest                    # run all tests

uv run pytest tests/test_config.py -k name   # run a single test
uv run ruff check src tests     # lint (rules in pyproject.toml)
uv run ruff format src tests    # format
```

Production runs as systemd unit `chat-bot` on this Oracle box:
`sudo systemctl restart chat-bot` after code changes; logs via `journalctl -u chat-bot -f`.

## What this is

Discord bot for the Involution Hell community. It bridges Discord → the involution-hell backend's internal API (`POST /api/community/links/internal` at `127.0.0.1:8080`, authed with `X-Internal-Key`). The bot **deliberately does not** touch the database, fetch OG metadata, or moderate content — all of that lives in the backend's `SharedLinkEnrichmentWorker` pipeline (`PENDING → APPROVED / PENDING_MANUAL / FLAGGED / REJECTED / ARCHIVED`). Don't add DB access or moderation logic to the bot; a previous attempt to write tables directly was rejected because it bypassed backend review.

All Discord submissions attach to the `discord-bridge` system account; the real submitter's name travels in `submitterLabel` / `recommendation`.

## Architecture

`src/chat_bot/__main__.py` builds a `commands.Bot`, stores `Settings` on `bot.chatbot_settings`, and loads ten cogs in `setup_hook`:

- **cogs/listener.py** — `on_message` in `DISCORD_WATCH_CHANNEL_IDS`: extract URLs, skip noise (Discord/CDN hosts, sticker/GIF aggregators, bare media-file URLs, own-org GitHub dev sub-paths like PRs/issues — see `_should_skip`), submit to backend, reply immediately, then background-poll `GET /internal/{id}` (2s interval, 30s cap) and reply again with the terminal status.
- **cogs/commands.py** — `/share <url> [recommendation]` slash command. Posts the URL as plain text publicly (Discord unfurls the OG card) with a `-# ` subtext status line, then background-polls and `message.edit`s the status when terminal. Poll constants are intentionally duplicated with listener.py to avoid cross-cog imports.
- **cogs/digest.py** — daily admin summary at `DIGEST_TIME_CST` (Asia/Shanghai) to the admin channel and/or email; skips silently when there's nothing to report or the sink isn't configured.
- **cogs/alerts.py** — embedded aiohttp server on `0.0.0.0:CHATBOT_ALERT_PORT` receiving webhooks: `/alert/flagged`（后端 FLAGGED 实时告警，X-Internal-Key + 可选 HMAC）、`/alert/mc`（mc-chat-bot 的玩家上线/稀有成就 → 社区频道播报）、`/alert/invest`（openInvest 事件/verdict 报警 → DM 给 `INVEST_ALERT_DM_USER_ID`，那个 DM 频道同时是 Hermes 会话，回复即可让 agent 就地分析）。Binds 0.0.0.0 because the backend is in Docker. Must not `wait_until_ready` in `cog_load` — that deadlocks setup_hook.
- **cogs/welcome.py** — 成员生命周期零 token 播报：新人欢迎（`MessageType.new_member` 系统消息，不需要 GUILD_MEMBERS 特权 intent）、Boost 感谢、入服整周年贺词（借道日常消息的 `author.joined_at`，北京时间口径，按年去重）。
- **cogs/starboard.py** — 精华墙：消息 ⭐ 达 `STARBOARD_THRESHOLD`（默认 3）转贴到 `STARBOARD_CHANNEL_ID`；按"墙上转贴消息 id"防套娃（墙可以就设在普通频道）；状态在 `.state/starboard.json`。
- **cogs/mc.py** — `/mc` slash 命令，mcstatus Server List Ping 查 MC 服在线状态（公开协议，无凭据）。
- **cogs/weekly.py** — 每周五 20:00 (CST) 社区周报（新成员/分享上架/精华上墙计数，来自 stats.py），全零静默。
- **cogs/github_feed.py** — 轮询 InvolutionHell org 公开事件（5 min），新 PR / PR 合并 / Release 播报到社区频道。两个实测坑已内置：事件 ID **不是**全局单调（用已见 ID 集合去重，别改回水位线比大小）；公开事件流的 PR payload 被精简（无 title/merged/html_url，需补拉 PR 详情，拉不到降级）。作者名会尝试解析成 Discord @mention（精确匹配 username/global_name/昵称；人工映射表 `.state/github_discord_map.json`）。

Supporting modules:

- **state.py / stats.py / milestones.py** — 社区小功能的本地 JSON 状态（`.state/`，gitignored）：去重记录、周报计数、分享过审里程碑（第 1/10/50/100 条庆祝；listener 和 commands 都调 milestones，避免 cog 间互相 import）。

- **api_client.py** — the only backend surface (submit / fetch_link / fetch_summary). Raises `DuplicateURL` on 409, `InternalAPIError` otherwise; `_safe_json` guards against non-JSON error bodies.
- **urls.py** — ALL outward-facing involutionhell.com links with UTM params are centralized here. Never hardcode site links in cogs; add a new function per (source, medium, campaign) context.
- **email_sender.py** — Gmail SMTP via App Password; `send_email` never raises, returns bool.
- **config.py** — pydantic-settings reading the **backend's shared .env** (`/home/ubuntu/involution-hell-project/backend/.env`, override with `CHATBOT_ENV_FILE` — note it's read at module import time). `extra="ignore"` because that file holds other services' keys. `.env.example` documents only the keys this bot needs.

## Conventions

- Fire-and-forget `asyncio.create_task` coroutines are always wrapped in `_safe()` so failures hit the log instead of vanishing; Discord replies go through `_safe_reply` / caught `HTTPException`.
- Logging is structlog key-value events (`log.info("share_ingested", url=..., ...)`) → stdout → journalctl. Follow the existing event-name style.
- Comments, log notes, and all user-facing Discord/email strings are in Chinese; keep that.
- 所有面向社区的文案走**艾露猫 persona**（`~/.hermes/SOUL.md`）：自称艾露猫、对每个人都叫"老大"（不是主人专属）、一条消息最多一个"喵"、✨🐾 少量点缀。注意本 bot 与 Hermes 共用同一个 Discord bot 账号——不要加关键词自动回复类功能，会和 Hermes 抢话。
- Tests isolate env via monkeypatch (see `tests/test_config.py`) — they must never depend on the real shared .env.
