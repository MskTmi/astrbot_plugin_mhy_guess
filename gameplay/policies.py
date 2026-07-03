"""
游戏策略判定

纯逻辑模块，无 I/O，易于单元测试
每个 Policy 类提供一个 evaluate 静态方法，
返回 PolicyDecision 表示是否允许操作
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyDecision:
    """策略判定结果allowed=True 时 reason 为空"""

    allowed: bool
    reason: str


class QuotaPolicy:
    """每日挑战次数限制策略"""

    @staticmethod
    def evaluate(
        unlimited: bool,
        quota_used: int,
        quota_limit: int,
    ) -> PolicyDecision:
        if unlimited:
            return PolicyDecision(allowed=True, reason="")
        if quota_used >= quota_limit:
            return PolicyDecision(
                allowed=False,
                reason=f"今日挑战次数已用完（{quota_used}/{quota_limit}），明天再来吧",
            )
        return PolicyDecision(allowed=True, reason="")


class CooldownPolicy:
    """游戏冷却时间策略"""

    @staticmethod
    def evaluate(
        unlimited: bool,
        remaining_seconds: float,
    ) -> PolicyDecision:
        if unlimited:
            return PolicyDecision(allowed=True, reason="")
        if remaining_seconds > 0:
            seconds = int(remaining_seconds) + 1
            return PolicyDecision(
                allowed=False,
                reason=f"游戏冷却中，请等待 {seconds} 秒",
            )
        return PolicyDecision(allowed=True, reason="")


class DuplicateRoomPolicy:
    """重复房间检测策略：同一会话不允许同时存在多个游戏"""

    @staticmethod
    def evaluate(room_exists: bool) -> PolicyDecision:
        if room_exists:
            return PolicyDecision(
                allowed=False,
                reason="当前会话已有进行中的游戏，请等待结束",
            )
        return PolicyDecision(allowed=True, reason="")
