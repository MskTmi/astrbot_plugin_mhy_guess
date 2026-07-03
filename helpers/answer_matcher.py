"""
答案匹配工具

利用 image_index 仓库的 alias-map.json 进行归一化匹配
支持：
  - 精确别名匹配（草履虫 → Kiana）
  - 大小写不敏感
  - 前后空格忽略
  - 多角色组合匹配（一张图含多个角色时，任一顺序、任意分隔、
    任意角色的别名拼接都算正确，例如 “琪亚娜芽衣” / “芽衣琪亚娜”
    / “芽衣草履虫” / “琪亚娜 芽衣”）
"""

from __future__ import annotations


def normalize_answer(text: str) -> str:
    """
    对用户输入进行归一化处理：
      - 去除首尾空白
      - 统一小写
    """
    return text.strip().lower()


def _build_surface_forms(
    expected_entity_ids: list[str],
    alias_map: dict[str, str],
    entity_index: dict[str, dict],
) -> dict[str, str]:
    """
    构造“表层形式 → canonical_id”字典，仅收录会映射到 expected_entity_ids
    的别名 / canonical id / display_name。

    收录范围限定在期望角色内，避免把无关角色的别名片段误当作分段
    匹配的成功段（例如 “芽衣” 在另一张图里恰好也是别的角色别名）。
    """
    forms: dict[str, str] = {}
    expected_set = set(expected_entity_ids)

    for alias, cid in alias_map.items():
        if cid in expected_set:
            forms[alias] = cid

    for eid in expected_entity_ids:
        forms[eid.lower()] = eid
        entity_data = entity_index.get(eid)
        if entity_data is not None:
            display_name = entity_data.get("display_name", "")
            if display_name:
                forms[display_name.lower()] = eid
            # 同时收录 entity 内置的 aliases 字段（如有）
            for a in entity_data.get("aliases", []) or []:
                if isinstance(a, str) and a:
                    forms[a.lower()] = eid

    return forms


def _segment_part(
    part: str, surface_forms: dict[str, str]
) -> bool:
    """
    判断单个（已去掉空白分隔的）片段是否可以完全切分为若干表层形式。

    使用记忆化回溯：在 part 的每个起点，尝试所有以该点开头的表层形式，
    命中则递归剩余子串。能完整覆盖 part 即视为成功。
    """
    n = len(part)
    if n == 0:
        return False

    # 记忆化：i → 从 i 开始是否能完整切分
    memo: dict[int, bool] = {}

    def can_from(i: int) -> bool:
        if i == n:
            return True
        if i in memo:
            return memo[i]
        result = False
        # 尝试所有以 part[i:] 开头的表层形式
        for form in surface_forms:
            length = len(form)
            if length == 0:
                continue
            if part.startswith(form, i):
                if can_from(i + length):
                    result = True
                    break
        memo[i] = result
        return result

    return can_from(0)


def check_answer(
    user_input: str,
    expected_entity_ids: list[str],
    alias_map: dict[str, str],
    entity_index: dict[str, dict],
) -> bool:
    """
    判断用户输入是否匹配候选角色

    参数：
      user_input           : 用户输入的原始文本
      expected_entity_ids  : 所有正确答案的 canonical id 列表（多角色图猜对任一即可）
      alias_map            : alias → canonical_id 映射
      entity_index         : entity_id → entity_data 映射

    匹配规则：
      1. 将用户输入归一化
      2. 单角色命中：alias_map 查到 resolved_id ∈ expected，或直接等于
         canonical id / display_name
      3. 多角色组合：把输入按空白切分为若干片段，每个片段再用表层形式
         完全切分；只要所有片段都能被切分成 expected 角色的表层形式，
         即视为正确（覆盖 “琪亚娜芽衣” / “芽衣琪亚娜” / “芽衣草履虫”
         / “琪亚娜 芽衣” 这类任意顺序、任意分隔、别名混用的情况）
    """
    normalized = normalize_answer(user_input)
    if not normalized:
        return False

    # 单角色命中
    resolved_id = alias_map.get(normalized)
    if resolved_id is not None:
        return resolved_id in expected_entity_ids

    if normalized in [eid.lower() for eid in expected_entity_ids]:
        return True

    for eid in expected_entity_ids:
        entity_data = entity_index.get(eid)
        if entity_data is not None:
            display_name = entity_data.get("display_name", "")
            if normalized == display_name.lower():
                return True

    # 多角色组合命中
    surface_forms = _build_surface_forms(
        expected_entity_ids, alias_map, entity_index
    )
    if not surface_forms:
        return False

    # 先按空白切分，再对每个无空格片段做切分匹配；
    # 这样 “琪亚娜 芽衣” 与 “琪亚娜芽衣” 走同一条路径
    parts = [p for p in normalized.split() if p]
    if not parts:
        return False

    return all(_segment_part(p, surface_forms) for p in parts)
