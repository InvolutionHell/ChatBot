# ChatBot 容器镜像（可选，Oracle 上直接 systemd 跑更简单）
FROM python:3.12-slim

# 装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# 先拷 metadata 让 layer cache 生效
COPY pyproject.toml README.md ./
COPY src/ ./src/

# 一次性装依赖（不用 lock 文件，容器里每次都是干净环境）
RUN uv sync --no-dev --frozen=false

# .env 通过 -v 或 docker compose 的 env_file 灌进来
ENV CHATBOT_ENV_FILE=/app/.env

ENTRYPOINT ["uv", "run", "--no-sync", "chat-bot"]
