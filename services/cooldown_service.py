"""
冷却管理服务

使用内存字典记录每个会话的冷却结束时间
重启后冷却自动失效（这是可接受的行为，因为重启意味着 bot 下线了一段时间，冷却已自然消耗）
"""

from __future__ import annotations

import time


class CooldownService:
    """基于内存的会话级冷却管理"""

    def __init__(self) -> None:
        # conversation_id → 冷却结束时间戳
        self._cooldowns: dict[str, float] = {}

    def set_cooldown(self, conversation_id: str, duration_seconds: int) -> None:
        """设置冷却"""
        end_time = time.time() + duration_seconds
        self._cooldowns[conversation_id] = end_time

    def get_remaining(self, conversation_id: str) -> float:
        """
        获取剩余冷却秒数
        若无冷却或已过期，返回 0.0
        """
        end_time = self._cooldowns.get(conversation_id)
        if end_time is None:
            return 0.0
        remaining = end_time - time.time()
        if remaining <= 0:
            # 清理过期条目
            self._cooldowns.pop(conversation_id, None)
            return 0.0
        return remaining

    def clear(self, conversation_id: str) -> None:
        """手动清除冷却"""
        self._cooldowns.pop(conversation_id, None)

    def has_any(self) -> bool:
        """是否存在任何未过期的冷却记录"""
        now = time.time()
        for end_time in self._cooldowns.values():
            if end_time > now:
                return True
        return False
