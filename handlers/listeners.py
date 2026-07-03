"""
回答监听器

薄层封装：从事件中提取消息文本，委托给 Coordinator 判定答案
不包含业务逻辑、不维护状态、不直接操作数据库
"""

from __future__ import annotations

from typing import Optional

from ..gameplay.coordinator import GameCoordinator


async def handle_answer(
    coordinator: GameCoordinator,
    conversation_id: str,
    message_text: str,
    answerer_id: str,
    answerer_name: str,
) -> Optional[tuple[str, str]]:
    """
    处理可能的答案消息

    Returns:
        (结果消息文本, 原图路径)（答对时），或 None（无活跃房间 / 答案错误 / 非文本消息）
    """
    # 跳过空消息
    if not message_text or not message_text.strip():
        return None

    # 跳过指令消息（以 / 开头）
    if message_text.strip().startswith("/"):
        return None

    # 委托给 Coordinator 判定
    try:
        result = await coordinator.submit_answer(
            conversation_id=conversation_id,
            user_input=message_text,
            answerer_id=answerer_id,
            answerer_name=answerer_name,
        )
    except Exception:
        import logging

        logger = logging.getLogger("astrbot_plugin_mhy_guess.listeners")
        logger.exception("答案判定异常")
        return None

    return result
