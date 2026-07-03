"""
答案匹配工具

利用 image_index 仓库的 alias-map.json 进行归一化匹配
支持：
  - 精确别名匹配（草履虫 → Kiana）
  - 大小写不敏感
  - 前后空格忽略
"""

from __future__ import annotations


def normalize_answer(text: str) -> str:
    """
    对用户输入进行归一化处理：
      - 去除首尾空白
      - 统一小写
    """
    return text.strip().lower()


def check_answer(
    user_input: str,
    expected_entity_ids: list[str],
    alias_map: dict[str, str],
    entity_index: dict[str, dict],
) -> bool:
    """
    判断用户输入是否匹配任意一个候选角色

    参数：
      user_input           : 用户输入的原始文本
      expected_entity_ids  : 所有正确答案的 canonical id 列表（多角色图猜对任一即可）
      alias_map            : alias → canonical_id 映射
      entity_index         : entity_id → entity_data 映射

    匹配规则：
      1. 将用户输入归一化
      2. 在 alias_map 中查找归一化后的 key，得到 resolved canonical_id
      3. 如果 resolved_id 在 expected_entity_ids 中，则正确
      4. 回退：检查归一化输入是否等于任一 canonical_id 或 display_name
    """
    normalized = normalize_answer(user_input)

    # 尝试别名映射
    resolved_id = alias_map.get(normalized)
    if resolved_id is not None:
        return resolved_id in expected_entity_ids

    # 回退：直接与 canonical id 和 display_name 比较
    if normalized in [eid.lower() for eid in expected_entity_ids]:
        return True

    for eid in expected_entity_ids:
        entity_data = entity_index.get(eid)
        if entity_data is not None:
            display_name = entity_data.get("display_name", "")
            if normalized == display_name.lower():
                return True

    return False
