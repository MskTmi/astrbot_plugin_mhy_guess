"""
统计服务

封装 MetricsStorage 的业务层接口，
为 Coordinator 提供语义化的统计操作方法
"""

from __future__ import annotations

from ..persistence.storage import MetricsStorage


class MetricService:
    """参与者统计的业务层封装"""

    def __init__(self, storage: MetricsStorage) -> None:
        self._storage = storage

    async def get_effective_quota(self, account_id: str, today: str) -> int:
        """获取当日已使用次数（跨日自动归零）"""
        return await self._storage.get_effective_quota_used(account_id, today)

    async def record_attempt(
        self, account_id: str, display_name: str, today: str
    ) -> None:
        """记录一次游戏尝试（attempts_total +1, quota_used +1）"""
        await self._storage.record_attempt(account_id, display_name, today)

    async def record_success(self, account_id: str) -> None:
        """记录一次猜对（success_total +1）"""
        await self._storage.record_success(account_id)
