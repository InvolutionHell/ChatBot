"""milestones 计数与贺词测试。"""

from __future__ import annotations

from chat_bot.milestones import milestone_message, record_approval


def test_milestone_counting(tmp_path, monkeypatch) -> None:
    from chat_bot import state as state_mod

    monkeypatch.setattr(state_mod, "_STATE_DIR", tmp_path)

    assert record_approval(1) == 1
    # 第 2~9 条不是里程碑
    assert all(record_approval(1) is None for _ in range(8))
    assert record_approval(1) == 10
    # 不同用户互不影响
    assert record_approval(2) == 1


def test_milestone_message() -> None:
    assert "第一条" in milestone_message("<@1>", 1)
    assert "10" in milestone_message("<@1>", 10)
