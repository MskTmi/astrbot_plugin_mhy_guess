"""
图片效果处理器

职责：
  - 对原始图片应用可配置的处理效果
  - 每个效果独立开关 + 独立参数 + 独立难度分
  - 支持随机单效果、支持效果组合
  - 将处理结果以 bytes 形式返回，不落盘

效果列表：
  blur             高斯模糊
  shuffle_blocks   分块打乱
  horizontal_slice 横向切割
  vertical_slice   纵向切割
  crop_area        截取区域
  pixelate         像素化
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("astrbot_plugin_mhy_guess.image_processor")


# ──────────────────────────────────────────────
# 效果元数据注册表
# ──────────────────────────────────────────────


@dataclass(frozen=True)
class EffectMeta:
    """单个效果的元信息"""

    key: str
    display_name: str
    enabled: bool
    params: dict  # 效果参数，如 blur_radius, block_size 等


class EffectRegistry:
    """效果注册表，集中管理所有效果元数据"""

    EFFECT_DEFS: list[tuple[str, str, dict]] = [
        ("blur", "高斯模糊", {"blur_radius": 50}),
        ("shuffle_blocks", "分块打乱", {"block_size": "50,60,80"}),
        ("horizontal_slice", "横向切割", {"slice_count": 50}),
        ("vertical_slice", "纵向切割", {"slice_count": 70}),
        ("crop_area", "截取区域", {"crop_ratio": 0.15}),
        ("pixelate", "像素化", {"pixel_size": 24}),
    ]

    @classmethod
    def build_defaults(cls) -> dict[str, dict]:
        """构建默认的原始配置 dict（供 settings.py 和 _conf_schema.json 使用）"""
        result: dict[str, dict] = {}
        for key, display_name, params in cls.EFFECT_DEFS:
            entry: dict = {
                "enabled": True,
                "display_name": display_name,
            }
            for param_name, param_default in params.items():
                entry[param_name] = param_default
            result[key] = entry
        return result

    @classmethod
    def build_metas(cls, effects_config: dict) -> dict[str, EffectMeta]:
        """从配置 dict 构建 EffectMeta 映射"""
        metas: dict[str, EffectMeta] = {}
        for key, display_name, param_defaults in cls.EFFECT_DEFS:
            entry = effects_config.get(key, {})
            enabled = entry.get("enabled", True)
            params: dict = {}
            for pname, pdefault in param_defaults.items():
                params[pname] = entry.get(pname, pdefault)
            metas[key] = EffectMeta(
                key=key,
                display_name=display_name,
                enabled=enabled,
                params=params,
            )
        return metas


# ──────────────────────────────────────────────
# 效果应用算法（纯静态方法）
# ──────────────────────────────────────────────


class Effects:
    """图片效果算法集合所有方法均为纯函数，不依赖外部状态"""

    @staticmethod
    def _resolve_int_pool(value, default: int) -> int:
        """
        将配置值解析为生效的 int：

        - int            ：原样返回（须 > 0，否则回退 default）
        - str            ：按逗号分隔解析为 int 候选池，随机选取一个
        - list/tuple     ：每个元素按上述规则递归解析后随机选取
        - 其它/解析失败  ：回退 default

        用于支持「单个数值」与「多个数值随机选取」两种配置形式。
        """
        if isinstance(value, bool):
            # bool 是 int 的子类，排除以免把 True/False 当成 1/0
            return default
        if isinstance(value, int):
            return value if value > 0 else default
        if isinstance(value, (list, tuple)):
            pool = [Effects._resolve_int_pool(v, default) for v in value]
            pool = [v for v in pool if v > 0]
            return random.choice(pool) if pool else default
        if isinstance(value, str):
            pool: list[int] = []
            for part in value.split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    n = int(float(part))
                except ValueError:
                    continue
                if n > 0:
                    pool.append(n)
            return random.choice(pool) if pool else default
        return default

    @staticmethod
    def blur(img, blur_radius: int = 8):
        from PIL import ImageFilter

        return img.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    @staticmethod
    def shuffle_blocks(img, block_size=50):
        """
        分块打乱：将图片切为网格块，随机重排粘贴，确保无空白

        block_size 可为单个 int，或逗号分隔的多个数值（字符串/列表）。
        多个数值时，每次应用从其中随机选取一个，从而支持难度区间随机。
        解析失败或结果为空时回退到默认 50。
        """
        block_size = Effects._resolve_int_pool(block_size, default=50)
        w, h = img.size
        from PIL import Image

        result = Image.new(img.mode, (w, h))
        positions = [
            (x, y) for y in range(0, h, block_size) for x in range(0, w, block_size)
        ]
        shuffled = positions.copy()
        random.shuffle(shuffled)
        for (ox, oy), (nx, ny) in zip(positions, shuffled, strict=False):
            cw = min(block_size, w - ox)
            ch = min(block_size, h - oy)
            nw_val = min(block_size, w - nx)
            nh_val = min(block_size, h - ny)
            block = img.crop((ox, oy, ox + cw, oy + ch))
            if cw != nw_val or ch != nh_val:
                block = block.resize((nw_val, nh_val), 1)  # LANCZOS ≈ 1
            result.paste(block, (nx, ny))
        return result

    @staticmethod
    def horizontal_slice(img, slice_count: int = 8):
        """横向切割：切成若干水平长条，打乱后重拼"""
        w, h = img.size
        from PIL import Image

        slice_h = h // slice_count
        slices = []
        for i in range(slice_count):
            y0 = i * slice_h
            y1 = (i + 1) * slice_h if i < slice_count - 1 else h
            slices.append(img.crop((0, y0, w, y1)))
        random.shuffle(slices)
        result = Image.new(img.mode, (w, h))
        y_off = 0
        for sl in slices:
            result.paste(sl, (0, y_off))
            y_off += sl.size[1]
        return result

    @staticmethod
    def vertical_slice(img, slice_count: int = 8):
        """纵向切割：切成若干垂直长条，打乱后重拼"""
        w, h = img.size
        from PIL import Image

        slice_w = w // slice_count
        slices = []
        for i in range(slice_count):
            x0 = i * slice_w
            x1 = (i + 1) * slice_w if i < slice_count - 1 else w
            slices.append(img.crop((x0, 0, x1, h)))
        random.shuffle(slices)
        result = Image.new(img.mode, (w, h))
        x_off = 0
        for sl in slices:
            result.paste(sl, (x_off, 0))
            x_off += sl.size[0]
        return result

    @staticmethod
    def crop_area(img, crop_ratio: float = 0.5):
        """随机截取图片中的一个矩形子区域"""
        w, h = img.size
        cw = max(int(w * crop_ratio), 50)
        ch = max(int(h * crop_ratio), 50)
        mx = w - cw
        my = h - ch
        x = random.randint(0, mx) if mx > 0 else 0
        y = random.randint(0, my) if my > 0 else 0
        return img.crop((x, y, x + cw, y + ch))

    @staticmethod
    def pixelate(img, pixel_size: int = 12):
        """像素化：先缩小再放大到原尺寸，产生马赛克效果"""
        w, h = img.size
        sm_w = max(1, w // pixel_size)
        sm_h = max(1, h // pixel_size)
        small = img.resize((sm_w, sm_h), 0)  # NEAREST
        return small.resize((w, h), 0)


# ──────────────────────────────────────────────
# 效果调度器
# ──────────────────────────────────────────────

_EFFECT_DISPATCH = {
    "blur": Effects.blur,
    "shuffle_blocks": Effects.shuffle_blocks,
    "horizontal_slice": Effects.horizontal_slice,
    "vertical_slice": Effects.vertical_slice,
    "crop_area": Effects.crop_area,
    "pixelate": Effects.pixelate,
}


class ImageProcessor:
    """
    图片效果处理器

    全内存流式：读文件 → Pillow 处理 → BytesIO → 返回 bytes，不落盘

    用法：
        processor = ImageProcessor(effects_config)
        meta = processor.pick_random_effect()
        img_bytes = processor.apply(source_path, meta.key)
    """

    def __init__(self, effects_config: dict) -> None:
        self._metas = EffectRegistry.build_metas(effects_config)
        self._enabled_keys = [k for k, m in self._metas.items() if m.enabled]
        # 伪随机：记忆最近一次使用的 effect key，避免连续重复
        # 仅当可用效果 >= 2 时生效；效果为 1 个时退化为纯随机
        self._last_key: Optional[str] = None
        logger.info(
            "效果处理器初始化: %d/%d 效果已启用 (全内存处理)",
            len(self._enabled_keys),
            len(self._metas),
        )

    # ── 属性 ──────────────────────────────────

    @property
    def effect_metas(self) -> dict[str, EffectMeta]:
        return self._metas

    @property
    def has_any_enabled(self) -> bool:
        return len(self._enabled_keys) > 0

    # ── 随机选取 ──────────────────────────────

    def pick_random_effect(self) -> Optional[EffectMeta]:
        """
        从已启用的效果中随机选取一个

        采用伪随机策略：当可用效果 >= 2 时，排除上一次使用的 effect，
        避免连续两局出现相同效果（提升体验多样性）。
        仅 1 个可用效果时退化为纯随机（必然重复）。

        Returns:
            EffectMeta 或 None（无已启用效果时）
        """
        if not self._enabled_keys:
            return None
        # 候选池：多于 1 个效果时排除上次使用的，避免连续重复
        if len(self._enabled_keys) > 1 and self._last_key is not None:
            candidates = [k for k in self._enabled_keys if k != self._last_key]
        else:
            candidates = self._enabled_keys
        key = random.choice(candidates)
        self._last_key = key
        return self._metas[key]

    # ── 应用效果 ──────────────────────────────

    def apply(self, source_path: str, effect_key: str) -> Optional[bytes]:
        """
        对图片应用指定效果，全内存处理，返回 JPEG 字节

        Args:
            source_path : 原始图片的绝对路径
            effect_key  : 效果注册键（如 "light_blur"）

        Returns:
            处理后的 JPEG bytes，失败时返回 None
        """
        if effect_key not in self._metas:
            logger.warning("未知效果 key: %s", effect_key)
            return None

        meta = self._metas[effect_key]
        effect_func = _EFFECT_DISPATCH.get(effect_key)
        if effect_func is None:
            logger.warning("效果未注册: %s", effect_key)
            return None

        return self._apply_effect(source_path, effect_key, meta.params)

    def apply_all(self, source_path: str) -> list[tuple[EffectMeta, Optional[bytes]]]:
        """
        对图片应用所有已注册效果（不论是否启用），用于测试预览

        按 EFFECT_DEFS 顺序逐个应用，返回 (EffectMeta, bytes_or_None) 列表。
        单个效果失败不影响其他效果。

        Args:
            source_path : 原始图片的绝对路径

        Returns:
            [(EffectMeta, bytes), ...]，bytes 为 None 表示该效果应用失败
        """
        results: list[tuple[EffectMeta, Optional[bytes]]] = []
        for key, _display_name, _param_defaults in EffectRegistry.EFFECT_DEFS:
            meta = self._metas.get(key)
            if meta is None:
                continue
            img_bytes = self._apply_effect(source_path, key, meta.params)
            results.append((meta, img_bytes))
        return results

    def _apply_effect(
        self, source_path: str, effect_key: str, params: dict
    ) -> Optional[bytes]:
        """内部：对图片应用单个效果并返回 JPEG bytes"""
        effect_func = _EFFECT_DISPATCH.get(effect_key)
        if effect_func is None:
            logger.warning("效果未注册: %s", effect_key)
            return None

        try:
            from io import BytesIO
            from PIL import Image as PILImage

            with PILImage.open(source_path) as img:
                if img is None:
                    return None
                processed = effect_func(img, **params)
                # 统一输出为 RGB JPEG
                if processed.mode in ("RGBA", "P"):
                    processed = processed.convert("RGB")
                buf = BytesIO()
                processed.save(buf, "JPEG", quality=85)
                return buf.getvalue()
        except Exception:
            logger.exception(
                "应用效果 %s 失败 | source_path=%s, params=%s",
                effect_key,
                source_path,
                params,
            )
            return None
