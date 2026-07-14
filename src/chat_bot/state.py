"""极简 JSON 状态存取：社区小功能（精华墙 / 周年 / 里程碑）的本地去重记录。

Bot 不碰数据库（见 CLAUDE.md），这类轻量状态就近存项目下 .state/ 目录。
丢了最坏结果也只是重复庆祝一次，不值得上更重的方案。
"""

from __future__ import annotations

import json
from pathlib import Path

# src/chat_bot/state.py → 上溯三层到 ChatBot 项目根目录
_STATE_DIR = Path(__file__).resolve().parent.parent.parent / ".state"


def load(name: str) -> dict:
    try:
        return json.loads((_STATE_DIR / f"{name}.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save(name: str, data: dict) -> None:
    _STATE_DIR.mkdir(exist_ok=True)
    path = _STATE_DIR / f"{name}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    tmp.replace(path)  # 原子替换，避免写一半被读到
