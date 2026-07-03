"""
配置管理模块

将插件配置与业务逻辑解耦
所有配置项在此集中定义默认值，
业务代码通过 PluginSettings 实例读取，不直接访问原始 dict
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EffectConfig:
    """单个图片效果的配置"""

    enabled: bool = True
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RepositorySettings:
    """图片仓库配置"""

    url: str = "https://github.com/MskTmi/mhy-image-index.git"
    proxy_method: str = "direct"
    proxy_custom_url: str = ""

    @property
    def effective_url(self) -> str:
        """根据代理设置计算实际 clone 用的 URL"""
        if self.proxy_method == "direct":
            return self.url
        if self.proxy_method == "custom":
            prefix = self.proxy_custom_url.strip().rstrip("/")
            if prefix:
                return f"{prefix}/{self.url}"
            return self.url
        # 预设代理
        prefix = self.proxy_method.strip().rstrip("/")
        return f"{prefix}/{self.url}"


@dataclass(frozen=True)
class PluginSettings:
    """
    插件运行时配置（不可变）

    字段：
      daily_quota        : 每日游戏次数上限，-1 表示无限
      cooldown_duration  : 游戏结束后冷却秒数，-1 表示无冷却
      round_timeout      : 单局超时秒数
      repo_settings      : 图片仓库配置（地址 + 代理）
      effects            : 效果配置 dict（key → EffectConfig）
    """

    daily_quota: int = 10
    cooldown_duration: int = 60
    round_timeout: int = 60
    repo_settings: RepositorySettings = field(default_factory=RepositorySettings)
    effects: dict[str, EffectConfig] = field(default_factory=dict)

    @property
    def unlimited_quota(self) -> bool:
        return self.daily_quota < 0

    @property
    def unlimited_cooldown(self) -> bool:
        # cooldown_duration <= 0（0 或 -1）均视为无冷却
        return self.cooldown_duration <= 0


def build_settings(raw_config: dict[str, Any] | None) -> PluginSettings:
    """
    从 AstrBot 提供的原始配置 dict 构建 PluginSettings

    缺失字段使用默认值，多余字段忽略
    """
    if raw_config is None:
        return PluginSettings()

    daily_quota = _safe_int(raw_config.get("daily_quota"), 10)
    cooldown_duration = _safe_int(raw_config.get("cooldown_duration"), 60)
    round_timeout = _safe_int(raw_config.get("round_timeout"), 60)

    # 仓库配置：兼容旧的 image_repo_url，优先用 repository_settings
    raw_repo = raw_config.get("repository_settings")
    if isinstance(raw_repo, dict):
        repo_settings = RepositorySettings(
            url=raw_repo.get(
                "repository_url",
                "https://github.com/MskTmi/mhy-image-index.git",
            ),
            proxy_method=raw_repo.get("proxy_method", "direct"),
            proxy_custom_url=raw_repo.get("proxy_custom_url", ""),
        )
    else:
        # 回退兼容旧配置
        fallback_url = raw_config.get(
            "image_repo_url",
            "https://github.com/MskTmi/mhy-image-index.git",
        )
        repo_settings = RepositorySettings(url=fallback_url)

    from ..services.image_processor import EffectRegistry

    # 构建效果配置的默认值
    effects_defaults = EffectRegistry.build_defaults()
    raw_effects: dict = raw_config.get("effects", {})
    if not isinstance(raw_effects, dict):
        raw_effects = {}

    effects: dict[str, EffectConfig] = {}
    for ekey, edefaults in effects_defaults.items():
        entry = raw_effects.get(ekey, {})
        if not isinstance(entry, dict):
            entry = {}
        eparams: dict[str, Any] = {}
        for pname, pdefault in edefaults.items():
            if pname in ("enabled", "display_name"):
                continue
            eparams[pname] = entry.get(pname, pdefault)
        effects[ekey] = EffectConfig(
            enabled=entry.get("enabled", edefaults.get("enabled", True)),
            params=eparams,
        )

    return PluginSettings(
        daily_quota=daily_quota,
        cooldown_duration=cooldown_duration,
        round_timeout=round_timeout,
        repo_settings=repo_settings,
        effects=effects,
    )


def _safe_int(value: Any, default: int) -> int:
    """将任意值安全转为 int，失败则返回 default"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
