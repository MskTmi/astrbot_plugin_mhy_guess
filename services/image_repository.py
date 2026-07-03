"""
图片仓库管理

职责：
  - clone / 验证本地图片仓库（使用 GitPython）
  - 加载 dist/ 下的三个聚合索引 JSON
  - 提供随机题目抽取
  - 缓存索引避免重复解析

目录约定（mhy-image-index 仓库）：
  data/       → 优化后的图片文件（.jpg）
  meta/       → 单图元数据（.json）
  entities/   → 角色词典源文件（.json）
  dist/       → 聚合索引（image-index.json, entity-index.json, alias-map.json）
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from pathlib import Path
from typing import Optional

logger = logging.getLogger("astrbot_plugin_mhy_guess.image_repo")


class ImageRepository:
    """图片仓库：clone → 索引加载 → 随机抽题"""

    def __init__(
        self,
        repo_path: Path,
        repo_url: str,
    ) -> None:
        self._repo_path: Path = repo_path
        self._repo_url: str = repo_url

        # 索引缓存
        self._image_index: dict = {}
        self._entity_index: dict = {}
        self._alias_map: dict[str, str] = {}  # lowercase_key → canonical_id

        # 预计算：有图片的角色列表，用于高效随机抽题
        self._entities_with_images: list[tuple[str, list[str]]] = []

    # ── 生命周期 ──────────────────────────────

    async def initialize(self) -> None:
        """
        确保仓库存在并加载索引。

        clone 失败会向上抛出，让调用方（main.py）能向用户展示
        “插件初始化失败：<真实原因>”，而不是后续走 GameError
        含糊提示。索引加载失败仅记录日志（仅影响可用题数，不致命）。
        """
        try:
            await self._ensure_repo()
        except Exception:
            logger.exception("初始化图片仓库失败")
            raise
        try:
            self._load_indexes()
        except Exception:
            logger.exception("加载图片索引失败")

    # ── 仓库管理 ──────────────────────────────

    async def _ensure_repo(self) -> None:
        """
        检测仓库是否存在且完整：完整则跳过，否则 git clone。

        “完整”判定：.git 存在 **且** dist/ 下的聚合索引文件存在。
        若 .git 存在但索引缺失（上次 clone 被中断残留），视为损坏，
        删除后重新 clone，避免每次都因跳过 clone 而拿到空索引。
        """
        git_dir = self._repo_path / ".git"
        index_file = self._repo_path / "dist" / "image-index.json"

        if git_dir.exists() and index_file.exists():
            logger.info("图片仓库已存在且完整: %s", self._repo_path)
            return

        if git_dir.exists() and not index_file.exists():
            logger.warning(
                "检测到不完整的图片仓库（.git 存在但索引缺失），将清理后重新 clone: %s",
                self._repo_path,
            )
            await self._purge_repo_dir()

        logger.info("正在 clone 图片仓库: %s → %s", self._repo_url, self._repo_path)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._clone_repo)
        logger.info("图片仓库 clone 完成")

    async def _purge_repo_dir(self) -> None:
        """删除整个仓库目录（清理残缺 clone）"""
        import shutil

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, shutil.rmtree, str(self._repo_path), True  # ignore_errors=True
        )

    def _clone_repo(self) -> None:
        """同步 clone（在线程执行器中调用）"""
        import git

        self._repo_path.mkdir(parents=True, exist_ok=True)
        git.Repo.clone_from(self._repo_url, str(self._repo_path))

    # ── 更新 & 强制重拉 ─────────────────────

    async def update(self) -> str:
        """
        git pull 拉取最新图片数据

        Returns:
            操作结果描述文本
        """
        git_dir = self._repo_path / ".git"
        if not git_dir.exists():
            return "图片仓库尚未 clone，请先运行一次 /guess 触发自动初始化"

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._git_pull)
            if result:
                self._load_indexes()
                count = len(self._image_index.get("assets", {}))
                return f"图库更新成功！当前共 {count} 张图片"
            return "图库已是最新版本"
        except Exception as e:
            logger.exception("git pull 失败")
            return f"更新失败: {e}"

    def _git_pull(self) -> bool:
        """同步 git pull，返回是否拉取了新内容"""
        import shutil
        import subprocess

        if not shutil.which("git"):
            raise RuntimeError("系统未安装 Git，无法执行更新操作")

        result = subprocess.run(
            ["git", "-C", str(self._repo_path), "pull", "--ff-only"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"git pull 失败: {stderr}")
        return "Already up" not in (result.stdout + result.stderr)

    async def force_reclone(self) -> str:
        """
        删除本地仓库后强制重新 clone（放弃所有本地修改）

        Returns:
            操作结果描述文本
        """
        try:
            import shutil

            loop = asyncio.get_event_loop()
            if self._repo_path.exists():
                await loop.run_in_executor(None, shutil.rmtree, str(self._repo_path))
            await loop.run_in_executor(None, self._clone_repo)
            self._load_indexes()
            count = len(self._image_index.get("assets", {}))
            return f"图库已从远程重新拉取！当前共 {count} 张图片"
        except Exception as e:
            logger.exception("强制重新拉取失败")
            return f"强制重新拉取失败: {e}"

    # ── 索引加载 ──────────────────────────────

    def _load_indexes(self) -> None:
        """从 dist/ 目录加载三个聚合索引 JSON"""
        dist_dir = self._repo_path / "dist"

        # 1. image-index.json — 图片主索引（含 assets, games, entities 倒排）
        self._image_index = self._load_json(
            dist_dir / "image-index.json", "image-index.json"
        )

        # 2. entity-index.json — 角色词典聚合
        self._entity_index = self._load_json(
            dist_dir / "entity-index.json", "entity-index.json"
        )

        # 3. alias-map.json — 别名 → canonical_id 映射
        raw_alias_map = self._load_json(dist_dir / "alias-map.json", "alias-map.json")
        # 将 key 统一为小写，便于答案匹配时忽略大小写
        self._alias_map = {k.lower(): v for k, v in raw_alias_map.items()}

        # 预计算：筛选出有图片的角色
        entities_index = self._image_index.get("entities", {})
        self._entities_with_images = [
            (eid, img_ids) for eid, img_ids in entities_index.items() if img_ids
        ]

        asset_count = len(self._image_index.get("assets", {}))
        entity_count = len(self._entity_index)
        alias_count = len(self._alias_map)
        logger.info(
            "索引加载完成: %d 张图片, %d 个角色, %d 个别名",
            asset_count,
            entity_count,
            alias_count,
        )

    def _load_json(self, path: Path, label: str) -> dict:
        """安全加载 JSON 文件，失败返回空 dict"""
        if not path.exists():
            logger.warning("未找到 %s: %s", label, path)
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.warning("%s 格式异常，期望 object", label)
                return {}
            return data
        except (json.JSONDecodeError, OSError) as e:
            logger.error("加载 %s 失败: %s", label, e)
            return {}

    # ── 公共属性 ──────────────────────────────

    @property
    def alias_map(self) -> dict[str, str]:
        """别名映射（key 已小写化）"""
        return self._alias_map

    @property
    def entity_index(self) -> dict[str, dict]:
        """角色词典：canonical_id → {display_name, games, aliases}"""
        return self._entity_index

    @property
    def is_ready(self) -> bool:
        """索引是否已加载且有可用数据"""
        return len(self._entities_with_images) > 0

    # ── 随机抽题 ──────────────────────────────

    def pick_random_question(
        self,
    ) -> Optional[tuple[list[str], list[str], list[str], str]]:
        """
        随机抽取一道题目

        策略：
          1. 从有图片的角色中随机选一个
          2. 从该角色的图片列表中随机选一张
          3. 返回 (entity_ids, display_names, game_names, image_absolute_path)
             一张图可能包含多个角色，猜对任意一个即为正确

        Returns:
            (entity_ids, display_names, game_names, image_path) 或 None（无数据时）
        """
        if not self._entities_with_images:
            return None

        # 随机选角色
        entity_id, image_ids = random.choice(self._entities_with_images)

        # 随机选图片
        image_id = random.choice(image_ids)

        # 获取图片路径
        assets = self._image_index.get("assets", {})
        asset = assets.get(image_id)
        if asset is None:
            return None

        image_rel_path = asset.get("image", "")
        if not image_rel_path:
            return None

        image_path = self._repo_path / image_rel_path
        if not image_path.exists():
            logger.warning("图片文件不存在: %s", image_path)
            return None

        # 获取该图片的全部角色
        entity_ids = asset.get("entities", [entity_id])
        if not isinstance(entity_ids, list) or len(entity_ids) == 0:
            entity_ids = [entity_id]

        # 收集所有角色的展示名
        display_names: list[str] = []
        for eid in entity_ids:
            entity_data = self._entity_index.get(eid, {})
            display_names.append(entity_data.get("display_name", eid))

        # 收集游戏名
        game_ids = asset.get("games", [])
        game_names: list[str] = game_ids if isinstance(game_ids, list) else []

        return (entity_ids, display_names, game_names, str(image_path))
