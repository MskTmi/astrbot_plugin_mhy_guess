"""
游戏协调器（Coordinator）

职责：
  - 创建 / 查询 / 销毁房间
  - 生命周期管理（超时调度）
  - 冷却管理
  - 答案校验委托给 answer_matcher

禁止：
  - handler 维护状态
  - handler 直接操作数据库
  - handler 包含业务逻辑
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Awaitable, Callable, Optional

from ..config.settings import PluginSettings
from ..helpers.answer_matcher import check_answer
from ..services.cooldown_service import CooldownService
from ..services.image_processor import ImageProcessor
from ..services.image_repository import ImageRepository
from ..services.metric_service import MetricService
from .policies import CooldownPolicy, DuplicateRoomPolicy, QuotaPolicy
from .room import GameError, Room

logger = logging.getLogger("astrbot_plugin_mhy_guess.coordinator")

# 超时回调签名：(conversation_id, answer_display_text, image_path) -> None
TimeoutCallback = Callable[[str, str, str], Awaitable[None]]


class GameCoordinator:
    """
    游戏核心协调器

    管理所有活跃房间的生命周期，
    是 handler 与 services/persistence 之间的唯一桥梁
    """

    def __init__(
        self,
        settings: PluginSettings,
        image_repo: ImageRepository,
        image_processor: ImageProcessor,
        metric_service: MetricService,
        cooldown_service: CooldownService,
        on_timeout: TimeoutCallback,
    ) -> None:
        self._settings = settings
        self._image_repo = image_repo
        self._image_processor = image_processor
        self._metric_service = metric_service
        self._cooldown_service = cooldown_service
        self._on_timeout = on_timeout
        # conversation_id → Room
        self._rooms: dict[str, Room] = {}

    # ── 创建房间 ──────────────────────────────

    async def create_room(
        self,
        conversation_id: str,
        creator_id: str,
        creator_name: str,
    ) -> tuple[Room, str, Optional[bytes]]:
        """
        创建新游戏房间

        Returns:
            (room, original_image_path, processed_bytes_or_none)

        Raises:
            GameError: 策略校验不通过或图片库为空
        """
        # 1. 重复房间检测
        existing = self._rooms.get(conversation_id)
        if existing is not None and existing.is_active:
            decision = DuplicateRoomPolicy.evaluate(True)
            raise GameError(decision.reason)

        # 2. 冷却检测
        remaining = self._cooldown_service.get_remaining(conversation_id)
        cd_decision = CooldownPolicy.evaluate(
            self._settings.unlimited_cooldown, remaining
        )
        if not cd_decision.allowed:
            raise GameError(cd_decision.reason)

        # 3. 每日次数检测
        today = date.today().isoformat()
        quota_used = await self._metric_service.get_effective_quota(creator_id, today)
        q_decision = QuotaPolicy.evaluate(
            self._settings.unlimited_quota,
            quota_used,
            self._settings.daily_quota,
        )
        if not q_decision.allowed:
            raise GameError(q_decision.reason)

        # 4. 随机抽题
        question = self._image_repo.pick_random_question()
        if question is None:
            raise GameError("图片库为空或尚未初始化，无法开始游戏")

        entity_ids, display_names, game_names, image_path = question

        # 5. 应用图片效果（全内存，不落盘）
        # 必须应用一个效果：无效果时直接报错，不再静默发送原图
        if not self._image_processor.has_any_enabled:
            raise GameError("未启用任何图片效果，请在配置中至少启用一个效果")

        effect_meta = self._image_processor.pick_random_effect()
        if effect_meta is None:
            raise GameError("没有可用的图片效果，请检查配置")

        result_bytes = self._image_processor.apply(image_path, effect_meta.key)
        if result_bytes is None:
            raise GameError(f"图片效果「{effect_meta.display_name}」应用失败，请重试")

        processed_bytes = result_bytes
        effect_name = effect_meta.display_name

        # 6. 创建房间
        room = Room(
            conversation_id=conversation_id,
            expected_entity_ids=entity_ids,
            expected_display_names=display_names,
            game_names=game_names,
            image_path=image_path,
            processed_image_bytes=processed_bytes,
            effect_name=effect_name,
            creator_id=creator_id,
        )

        # 7. 调度超时任务
        expire_task = asyncio.create_task(self._schedule_timeout(conversation_id))
        room.expire_task = expire_task

        # 8. 注册房间
        self._rooms[conversation_id] = room

        # 9. 记录尝试（quota_used +1）
        await self._metric_service.record_attempt(creator_id, creator_name, today)

        logger.info(
            "房间已创建: conv=%s, entities=%s, effect=%s",
            conversation_id,
            entity_ids,
            effect_name,
        )

        return room, image_path, processed_bytes

    # ── 提交答案 ──────────────────────────────

    async def submit_answer(
        self,
        conversation_id: str,
        user_input: str,
        answerer_id: str,
        answerer_name: str,
    ) -> Optional[tuple[str, str]]:
        """
        提交答案

        Returns:
            (结果消息文本, 原图路径)（答对时），或 None（无活跃房间 / 答案错误）
        """
        room = self._rooms.get(conversation_id)
        if room is None:
            return None
        if not room.is_active:
            return None

        is_correct = check_answer(
            user_input=user_input,
            expected_entity_ids=room.expected_entity_ids,
            alias_map=self._image_repo.alias_map,
            entity_index=self._image_repo.entity_index,
        )

        if not is_correct:
            return None

        # 答对！结束房间
        display_text = " / ".join(room.expected_display_names)
        # 在清理房间前取出原图路径，用于答对后发送
        original_image_path = room.image_path
        try:
            room.finish()
        except Exception:
            logger.exception("结束房间失败: conv=%s", conversation_id)

        # 记录成功
        try:
            await self._metric_service.record_success(answerer_id)
        except Exception:
            logger.exception("记录答对失败: answerer=%s", answerer_id)

        # 应用冷却
        await self._apply_cooldown(conversation_id)

        # 清理房间
        self._cleanup_room(conversation_id)

        logger.info(
            "回答正确: conv=%s, answerer=%s, entities=%s",
            conversation_id,
            answerer_id,
            room.expected_entity_ids,
        )

        return f"回答正确！答案是：{display_text}", original_image_path

    # ── 查询 ──────────────────────────────────

    def get_active_room(self, conversation_id: str) -> Optional[Room]:
        """获取指定会话的活跃房间，不存在返回 None"""
        room = self._rooms.get(conversation_id)
        if room is not None and room.is_active:
            return room
        return None

    def has_any_active_room(self) -> bool:
        """是否存在任何活跃房间（用于消息监听器的快速判断）"""
        for room in self._rooms.values():
            if room.is_active:
                return True
        return False

    # ── 超时调度 ──────────────────────────────

    async def _schedule_timeout(self, conversation_id: str) -> None:
        """异步超时任务：到期后结束房间并触发回调"""
        try:
            await asyncio.sleep(self._settings.round_timeout)

            room = self._rooms.get(conversation_id)
            if room is None:
                return
            if not room.is_active:
                return

            # 超时
            answer_text = " / ".join(room.expected_display_names)
            image_path = room.image_path
            room.time_out()

            # 应用冷却
            await self._apply_cooldown(conversation_id)

            # 清理房间
            self._cleanup_room(conversation_id)

            logger.info(
                "游戏超时: conv=%s, entities=%s",
                conversation_id,
                room.expected_entity_ids,
            )

            # 触发超时回调（发送超时消息 + 原图）
            await self._on_timeout(conversation_id, answer_text, image_path)

        except asyncio.CancelledError:
            # 房间提前结束（答对），超时任务被取消
            pass

    # ── 冷却 ──────────────────────────────────

    async def _apply_cooldown(self, conversation_id: str) -> None:
        """对会话应用冷却"""
        if self._settings.unlimited_cooldown:
            return
        self._cooldown_service.set_cooldown(
            conversation_id, self._settings.cooldown_duration
        )

    # ── 清理 ──────────────────────────────────

    def _cleanup_room(self, conversation_id: str) -> None:
        """从内存中移除房间"""
        self._rooms.pop(conversation_id, None)

    def abort_room(self, conversation_id: str, reason: str = "") -> None:
        """
        异常中止指定会话的活跃房间（不应用冷却）

        用于发送图片失败等系统错误场景：房间未正常完成，
        但需释放会话槽位，避免阻塞后续游戏。不记录成功/超时指标。

        Args:
            conversation_id : 会话唯一标识
            reason          : 中止原因（仅用于日志）
        """
        room = self._rooms.get(conversation_id)
        if room is None:
            return
        if not room.is_active:
            # 已结束（答对/超时），仅清理残留
            self._cleanup_room(conversation_id)
            return

        try:
            room.finish()
        except Exception:
            logger.exception("中止房间失败: conv=%s", conversation_id)

        self._cleanup_room(conversation_id)
        logger.warning(
            "房间已异常中止: conv=%s, reason=%s, entities=%s",
            conversation_id,
            reason or "(未提供)",
            room.expected_entity_ids,
        )

    # ── 关闭 ──────────────────────────────────

    async def shutdown(self) -> None:
        """关闭所有活跃房间（插件卸载时调用）"""
        for room in list(self._rooms.values()):
            room.finish()
        self._rooms.clear()
        logger.info("协调器已关闭，所有房间已清理")
