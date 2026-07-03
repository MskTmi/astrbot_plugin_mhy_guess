"""
游戏房间状态管理

每个 Room 代表一局正在进行的游戏
生命周期：ACTIVE → FINISHED（答对） / TIMED_OUT（超时）
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class RoomStatus(Enum):
    """房间状态枚举"""

    ACTIVE = "active"
    FINISHED = "finished"
    TIMED_OUT = "timed_out"


class GameError(Exception):
    """游戏业务异常，用于策略校验不通过等场景"""

    pass


@dataclass
class Room:
    """
    单个游戏房间的运行时状态

    Attributes:
        conversation_id       : 会话唯一标识（unified_msg_origin）
        expected_entity_ids   : 图片中所有角色的 canonical id 列表（猜对任一即可）
        expected_display_names: 图片中所有角色的展示名列表
        game_names            : 图片所属游戏名列表（用于提示）
        image_path            : 原始图片的本地绝对路径
        processed_image_path  : 应用效果后的图片路径（可能等于 image_path）
        effect_name           : 应用的效果名称（"无效果" 表示未启用）
        creator_id            : 发起者 account_id
        opened_at             : 房间创建时间戳
        status                : 当前状态
        expire_task           : 超时 asyncio.Task
    """

    conversation_id: str
    expected_entity_ids: list[str]
    expected_display_names: list[str]
    game_names: list[str]
    image_path: str
    creator_id: str
    processed_image_bytes: Optional[bytes] = None
    effect_name: str = "无效果"
    opened_at: float = field(default_factory=time.time)
    status: RoomStatus = RoomStatus.ACTIVE
    expire_task: Optional[asyncio.Task] = field(default=None, repr=False)

    @property
    def is_active(self) -> bool:
        """房间是否仍在进行中"""
        return self.status == RoomStatus.ACTIVE

    def finish(self) -> None:
        """标记为已完成（有人答对），并取消超时任务"""
        self.status = RoomStatus.FINISHED
        self._cancel_expire_task()

    def time_out(self) -> None:
        """标记为超时结束"""
        self.status = RoomStatus.TIMED_OUT

    def _cancel_expire_task(self) -> None:
        """安全取消超时任务"""
        if self.expire_task is not None:
            if not self.expire_task.done():
                self.expire_task.cancel()
            self.expire_task = None
