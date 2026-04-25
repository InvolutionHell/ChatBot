# ChatBot

Involution Hell 社群 Discord 机器人。**从 Discord 群转链接 → 调 involution-hell 后端 internal API → 主站 `/share` 统一展示**。

## 做什么

- 监听指定 Discord 频道（如 `#分享`）里含链接的消息
- 提链后调后端 `POST /api/community/links/internal`，**由后端统一做 OG 抓取 + DeepSeek 审核 + 白名单 + 去重**
- 提供 `/share <url>` slash command 手动提交

> ⚠️ Bot **不直连数据库，不抓 OG，不做审核**。所有内容都走后端既有的 `SharedLinkEnrichmentWorker`，
> 保持和主站用户手动提交完全一致的管线（PENDING → APPROVED/PENDING_MANUAL/FLAGGED）。
> 所有 Discord 提交挂到 `discord-bridge` 系统账号，真实提交人写在 `recommendation` 字段里（`来自 Discord @xxx`）。

## 架构

```
Discord 群消息
      ↓ on_message / /share
   [ChatBot]
      ↓ POST /api/community/links/internal
      ↓ X-Internal-Key: <共享密钥>
[involution-hell 后端] (127.0.0.1:8080)
   ├─ UrlNormalizer 规范化 + 去重
   ├─ insert shared_links (status=PENDING)
   └─ @Async SharedLinkEnrichmentWorker
        ├─ OgFetchService 抓 OG
        ├─ ClassificationService → DeepSeek 审核 + 分类
        └─ 决定终态：APPROVED / PENDING_MANUAL / FLAGGED
              ↓
主站 involutionhell.com/share 页面自动展示（仅 APPROVED）
```

**关键**：Bot 和后端同机（Oracle），走 `http://127.0.0.1:8080` 直连，**不经过 Caddy / Cloudflare**，无公网暴露风险。

## 技术栈

| | |
|---|---|
| Python | 3.12 |
| 包管理 | [uv](https://github.com/astral-sh/uv) |
| Discord | [discord.py](https://github.com/Rapptz/discord.py) 2.x |
| HTTP | httpx |
| 配置 | pydantic-settings |
| 日志 | structlog → journalctl |
| 部署 | systemd / Dockerfile 备用 |

## 目录

```
ChatBot/
├── pyproject.toml
├── .env.example              # 展示需要往 involution-hell .env 里加的键
├── chat-bot.service          # systemd unit
├── Dockerfile                # 可选容器化
├── src/chat_bot/
│   ├── __main__.py           # 入口
│   ├── config.py             # 读 involution-hell .env
│   ├── api_client.py         # 调后端 /api/community/links/internal
│   └── cogs/
│       ├── listener.py       # on_message 自动提链
│       └── commands.py       # /share slash command
└── tests/
```

## 前置条件

1. **involution-hell 后端已部署，版本 ≥ feat/discord-internal-submit 分支**（包含 `POST /api/community/links/internal` 和 discord-bridge seed）
2. **后端 `.env` 已写 `INTERNAL_API_KEY`**，并已重启生效（`openssl rand -hex 32` 随机生成）
3. **Postgres 已跑过 schema.sql seed**（discord-bridge 账号 id 在 user_accounts 表里）

## 首次部署（Oracle）

### 1. 建 Discord Application

1. https://discord.com/developers/applications → New Application
2. 左栏 **Bot** → Reset Token → 拷出来
3. **打开 Message Content Intent**
4. 左栏 **OAuth2 → URL Generator**：
   - Scopes：`bot`、`applications.commands`
   - Bot Permissions：`View Channels`、`Send Messages`、`Embed Links`、`Read Message History`、`Use Slash Commands`
   - 邀请链接把 bot 拉进 server

### 2. 往 involution-hell 后端 .env 追加

```dotenv
# Discord ChatBot
DISCORD_BOT_TOKEN=...
DISCORD_WATCH_CHANNEL_IDS=1234567890,9876543210
DISCORD_GUILD_ID=1122334455

# 机器人桥接密钥（和后端 application.properties 的 internal.api-key 一致）
INTERNAL_API_KEY=...（openssl rand -hex 32）
```

### 3. 跑起来

```bash
cd /home/ubuntu/involution-hell-project/ChatBot
uv sync
uv run chat-bot
```

看到 `event='bot_ready' user='InvolutionHellChatBot#...'` 就是成功。

### 4. 注册 systemd

```bash
sudo cp chat-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now chat-bot
sudo journalctl -u chat-bot -f
```

## 日常运维

| 操作 | 命令 |
|---|---|
| 看日志 | `journalctl -u chat-bot -f` |
| 重启 | `sudo systemctl restart chat-bot` |
| 改代码后 | `sudo systemctl restart chat-bot` |
| 改 .env | 改 `/home/ubuntu/involution-hell-project/backend/.env` → `sudo systemctl restart chat-bot`（后端也要重启） |
| 查库 | `docker exec -it involution-postgres psql -U $PGUSER -d $PGDATABASE -c "SELECT id, host, status, recommendation, created_at FROM shared_links WHERE submitter_id = (SELECT id FROM user_accounts WHERE username='discord-bridge') ORDER BY created_at DESC LIMIT 20;"` |

## 设计取舍

- **不重复造轮子**：OG 抓取 / DeepSeek 审核 / 白名单 / 举报 / 归档 这套管线后端已经有了，
  Bot 走一样的 API 就行。之前试过在 Bot 侧直写 `share.submission` 表，会绕开后端审核，不合格。
- **discord-bridge 系统账号**：避免给每个 Discord 用户建 `user_accounts` 行（otherwise 用户清理会变噩梦）。
  真实提交人名放 `recommendation` 字段里，主站展示时用这个。
- **不做反向广播**：MVP 阶段只做「被动收集」，别在群里刷屏打扰大家。
- **Bot ↔ 后端用 `127.0.0.1`**：同机内网通信，不经 Caddy，不用 TLS，最小攻击面。
  X-Internal-Key 是二道锁，防止别的容器或本机用户误访问。
