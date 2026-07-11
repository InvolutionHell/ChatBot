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

`src/chat_bot/__main__.py` builds a `commands.Bot`, stores `Settings` on `bot.chatbot_settings`, and loads four cogs in `setup_hook`:

- **cogs/listener.py** — `on_message` in `DISCORD_WATCH_CHANNEL_IDS`: extract URLs, skip noise (Discord/CDN hosts, sticker/GIF aggregators, bare media-file URLs, own-org GitHub dev sub-paths like PRs/issues — see `_should_skip`), submit to backend, reply immediately, then background-poll `GET /internal/{id}` (2s interval, 30s cap) and reply again with the terminal status.
- **cogs/commands.py** — `/share <url> [recommendation]` slash command. Posts the URL as plain text publicly (Discord unfurls the OG card) with a `-# ` subtext status line, then background-polls and `message.edit`s the status when terminal. Poll constants are intentionally duplicated with listener.py to avoid cross-cog imports.
- **cogs/digest.py** — daily admin summary at `DIGEST_TIME_CST` (Asia/Shanghai) to the admin channel and/or email; skips silently when there's nothing to report or the sink isn't configured.
- **cogs/alerts.py** — embedded aiohttp server on `0.0.0.0:CHATBOT_ALERT_PORT` (`/alert/flagged`) receiving real-time FLAGGED webhooks from the backend. Binds 0.0.0.0 because the backend is in Docker. Auth: constant-time `X-Internal-Key` compare + optional HMAC-SHA256 (`WEBHOOK_HMAC_SECRET`, verified against raw body). Must not `wait_until_ready` in `cog_load` — that deadlocks setup_hook.

Supporting modules:

- **api_client.py** — the only backend surface (submit / fetch_link / fetch_summary). Raises `DuplicateURL` on 409, `InternalAPIError` otherwise; `_safe_json` guards against non-JSON error bodies.
- **urls.py** — ALL outward-facing involutionhell.com links with UTM params are centralized here. Never hardcode site links in cogs; add a new function per (source, medium, campaign) context.
- **email_sender.py** — Gmail SMTP via App Password; `send_email` never raises, returns bool.
- **config.py** — pydantic-settings reading the **backend's shared .env** (`/home/ubuntu/involution-hell-project/backend/.env`, override with `CHATBOT_ENV_FILE` — note it's read at module import time). `extra="ignore"` because that file holds other services' keys. `.env.example` documents only the keys this bot needs.

## Conventions

- Fire-and-forget `asyncio.create_task` coroutines are always wrapped in `_safe()` so failures hit the log instead of vanishing; Discord replies go through `_safe_reply` / caught `HTTPException`.
- Logging is structlog key-value events (`log.info("share_ingested", url=..., ...)`) → stdout → journalctl. Follow the existing event-name style.
- Comments, log notes, and all user-facing Discord/email strings are in Chinese; keep that.
- Tests isolate env via monkeypatch (see `tests/test_config.py`) — they must never depend on the real shared .env.
