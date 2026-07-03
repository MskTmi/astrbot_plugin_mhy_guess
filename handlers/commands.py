"""
指令处理器

薄层封装：从事件中提取数据，委托给 Coordinator，返回结果
不包含业务逻辑、不维护状态、不直接操作数据库
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..gameplay.coordinator import GameCoordinator
from ..services.image_processor import EffectMeta, ImageProcessor
from ..services.image_repository import ImageRepository


@dataclass
class GuessCommandResult:
    """指令处理结果"""

    text: str
    image_path: Optional[str] = None  # 原图（无效果时用此发送）
    image_bytes: Optional[bytes] = None  # 效果处理后的 bytes（非 None 时优先使用）
    effect_name: str = "无效果"


async def handle_guess_command(
    coordinator: GameCoordinator,
    conversation_id: str,
    sender_id: str,
    sender_name: str,
) -> GuessCommandResult:
    """
    处理 /guess 指令

    Returns:
        GuessCommandResult：包含提示文本、图片路径/bytes和效果信息

    Raises:
        GameError：策略校验不通过
    """
    room, original_path, processed_bytes = await coordinator.create_room(
        conversation_id=conversation_id,
        creator_id=sender_id,
        creator_name=sender_name,
    )

    game_hint = f"所属游戏: {', '.join(room.game_names)}" if room.game_names else ""
    lines = [
        "猜猜这是谁？",
        f"图片效果: {room.effect_name}",
    ]
    if game_hint:
        lines.append(game_hint)
    return GuessCommandResult(
        text="\n".join(lines),
        image_path=original_path,
        image_bytes=processed_bytes,
        effect_name=room.effect_name,
    )


async def handle_update_repo(image_repo: ImageRepository) -> str:
    """处理 /更新猜角色图库 指令"""
    return await image_repo.update()


async def handle_force_reclone(image_repo: ImageRepository) -> str:
    """处理 /重新获取猜角色图库 指令"""
    return await image_repo.force_reclone()


@dataclass
class EffectTestItem:
    """单个效果的测试结果"""

    meta: EffectMeta
    image_bytes: Optional[bytes]  # 处理后的 JPEG bytes，None 表示失败


async def handle_test_effects(
    image_repo: ImageRepository,
    image_processor: ImageProcessor,
) -> tuple[Optional[str], list[EffectTestItem]]:
    """
    处理 /测试所有效果 指令

    随机选一张原图，对所有效果各应用一次，返回原图路径和各效果结果列表。

    Returns:
        (original_image_path, [EffectTestItem, ...])
        original_image_path 为 None 表示图片库为空
    """
    question = image_repo.pick_random_question()
    if question is None:
        return None, []

    _entity_ids, _display_names, _game_names, image_path = question
    results = image_processor.apply_all(image_path)
    items = [
        EffectTestItem(meta=meta, image_bytes=img_bytes)
        for meta, img_bytes in results
    ]
    return image_path, items
