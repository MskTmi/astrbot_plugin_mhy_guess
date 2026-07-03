"""
astrbot_plugin_mhy_guess — 米家看图猜角色插件

AstrBot 插件入口

职责：
  - 插件注册与生命周期管理
  - 懒初始化所有服务
  - 注册指令和消息监听器
  - 桥接 AstrBot 事件与 Coordinator
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Star

from .config.settings import PluginSettings, build_settings
from .gameplay.coordinator import GameCoordinator
from .gameplay.room import GameError
from .handlers.commands import (
    EffectTestItem,
    handle_force_reclone,
    handle_guess_command,
    handle_test_effects,
    handle_update_repo,
)
from .handlers.listeners import handle_answer
from .persistence.storage import MetricsStorage
from .services.cooldown_service import CooldownService
from .services.image_processor import ImageProcessor
from .services.image_repository import ImageRepository
from .services.metric_service import MetricService

PLUGIN_NAME = "astrbot_plugin_mhy_guess"
_log = logging.getLogger(PLUGIN_NAME)


def _get_data_dir() -> Path:
    """
    获取插件数据目录

    AstrBot 运行时 CWD 为 AstrBot 根目录，
    数据存放于 data/plugin_data/{plugin_name}/
    """
    return Path.cwd() / "data" / "plugin_data" / PLUGIN_NAME


def _repo_needs_clone() -> bool:
    """图片仓库是否尚未 clone（需要首次拉取）"""
    repo_git = _get_data_dir() / "image_index" / ".git"
    return not repo_git.exists()


class MhyGuessPlugin(Star):
    """米家看图猜角色插件主类"""

    def __init__(self, context, config) -> None:
        super().__init__(context)
        self._ctx = context
        # AstrBot 通过 __init__ 第二个参数注入 _conf_schema.json 解析后的配置
        # (AstrBotConfig 继承自 dict)。必须在 __init__ 接收，否则核心会回退到
        # 不传 config，导致插件配置完全失效（只能拿到 build_settings 的默认值）。
        self.config: dict = config if isinstance(config, dict) else {}
        self._coordinator: GameCoordinator | None = None
        self._image_repo: ImageRepository | None = None
        self._image_processor: ImageProcessor | None = None
        self._storage: MetricsStorage | None = None
        self._settings: PluginSettings | None = None
        self._initialized: bool = False
        self._init_lock = asyncio.Lock()

    # ── 懒初始化 ──────────────────────────────

    async def _ensure_initialized(self) -> None:
        """确保插件已初始化，首次调用时执行完整初始化流程"""
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            await self._do_initialize()
            self._initialized = True

    async def _do_initialize(self) -> None:
        """执行完整初始化：配置 → 存储 → 图片仓库 → 服务 → 协调器"""
        data_dir = _get_data_dir()

        # 1. 读取配置
        raw_config = self._read_config()
        self._settings = build_settings(raw_config)
        _log.info(
            "[%s] 配置已加载: cooldown=%ds, round_timeout=%ds, daily_quota=%d, "
            "enabled_effects=%d",
            PLUGIN_NAME,
            self._settings.cooldown_duration,
            self._settings.round_timeout,
            self._settings.daily_quota,
            sum(1 for e in self._settings.effects.values() if e.enabled),
        )

        # 2. 初始化 SQLite 存储
        db_path = data_dir / "database" / "runtime.db"
        self._storage = MetricsStorage(db_path)
        await self._storage.initialize()

        # 3. 初始化图片仓库（clone + 加载索引）
        repo_path = data_dir / "image_index"
        image_repo = ImageRepository(
            repo_path=repo_path,
            repo_url=self._settings.repo_settings.effective_url,
        )
        await image_repo.initialize()

        if not image_repo.is_ready:
            _log.warning("图片仓库索引为空，游戏可能无法正常进行")

        self._image_repo = image_repo

        # 4. 初始化图片效果处理器（全内存，不落盘）
        effects_config = self._settings.effects
        raw_effects_dict: dict = {}
        for ekey, econf in effects_config.items():
            entry: dict = {
                "enabled": econf.enabled,
            }
            entry.update(econf.params)
            raw_effects_dict[ekey] = entry
        image_processor = ImageProcessor(raw_effects_dict)
        self._image_processor = image_processor

        # 5. 初始化服务
        metric_service = MetricService(self._storage)
        cooldown_service = CooldownService()

        # 6. 初始化协调器
        self._coordinator = GameCoordinator(
            settings=self._settings,
            image_repo=image_repo,
            image_processor=image_processor,
            metric_service=metric_service,
            cooldown_service=cooldown_service,
            on_timeout=self._handle_timeout,
        )

        _log.info("[%s] 插件初始化完成，数据目录: %s", PLUGIN_NAME, data_dir)

    def _read_config(self) -> dict:
        """
        读取插件配置

        AstrBot 在实例化插件时通过 __init__ 的 config 参数注入
        _conf_schema.json 解析后的配置（AstrBotConfig，dict 子类），
        已与磁盘上的 <plugin>_config.json 合并、与 schema 默认值对齐。
        直接使用 self.config 即可拿到用户在 WebUI 设置的最新值。
        """
        if hasattr(self, "config") and isinstance(self.config, dict):
            return self.config
        _log.warning("[%s] 插件 config 未注入，使用默认配置", PLUGIN_NAME)
        return {}

    # ── 超时回调 ──────────────────────────────

    async def _handle_timeout(self, conversation_id: str, answer_text: str) -> None:
        """超时回调：向会话发送超时提示消息（含正确答案）"""
        try:
            # MessageChain 在 astrbot.api.event 重导出（来自
            # astrbot.core.message.message_event_result），不要从
            # message_components 导入（那里只有 Plain/Image 等组件类）
            from astrbot.api.event import MessageChain

            chain = MessageChain().message(f"时间到！正确答案是：{answer_text}")
            ok = await self._ctx.send_message(conversation_id, chain)
            if not ok:
                _log.warning(
                    "[%s] 超时消息未送达（未找到匹配平台）: conv=%s",
                    PLUGIN_NAME,
                    conversation_id,
                )
        except Exception:
            _log.exception(
                "[%s] 发送超时消息失败: conv=%s, answer=%s",
                PLUGIN_NAME,
                conversation_id,
                answer_text,
            )

    # ── 指令处理 ──────────────────────────────

    @filter.command("guess", alias=["猜角色"])
    async def on_guess_command(self, event: AstrMessageEvent):
        """处理 /guess 指令：开始一局猜角色游戏"""
        # 首次 clone 前发送提示，避免用户误以为卡死
        if not self._initialized and _repo_needs_clone():
            yield event.plain_result("⏳ 正在首次获取图片库，请耐心等待...")

        try:
            await self._ensure_initialized()
        except Exception as e:
            _log.error("[%s] 初始化失败: %s", PLUGIN_NAME, e)
            yield event.plain_result(f"插件初始化失败：{e}")
            return

        conversation_id = event.unified_msg_origin
        sender_id = self._get_sender_id(event)
        sender_name = self._get_sender_name(event)

        try:
            result = await handle_guess_command(
                coordinator=self._coordinator,
                conversation_id=conversation_id,
                sender_id=sender_id,
                sender_name=sender_name,
            )
        except GameError as e:
            _log.warning(
                "[%s] /guess 被策略拒绝: %s | conversation_id=%s, sender_id=%s",
                PLUGIN_NAME,
                e,
                conversation_id,
                sender_id,
            )
            yield event.plain_result(str(e))
            return

        # 发送图片：优先使用处理后的 bytes（全内存），无效果时用原图路径
        # 注意：MessageChain.file_image() / Image.fromFileSystem() 只接受路径，
        # 传 bytes 会失败。内存中的 JPEG bytes 必须用 Image.fromBytes()。
        if result.image_bytes:
            try:
                yield event.chain_result(
                    [
                        Plain(result.text + "\n"),
                        Image.fromBytes(result.image_bytes),
                    ]
                )
            except Exception:
                _log.exception(
                    "[%s] 发送处理后图片失败，image_bytes 长度=%d, effect=%s",
                    PLUGIN_NAME,
                    len(result.image_bytes),
                    result.effect_name,
                )
                # 发送失败时中止房间，避免阻塞后续游戏（不应用冷却）
                self._coordinator.abort_room(conversation_id, "发送处理后图片失败")
                yield event.plain_result("发送图片失败，请重试")
        elif result.image_path:
            try:
                yield event.chain_result(
                    [
                        Plain(result.text + "\n"),
                        Image.fromFileSystem(result.image_path),
                    ]
                )
            except Exception:
                _log.exception(
                    "[%s] 发送原图失败，image_path=%s",
                    PLUGIN_NAME,
                    result.image_path,
                )
                self._coordinator.abort_room(conversation_id, "发送原图失败")
                yield event.plain_result("发送图片失败，请重试")
        else:
            yield event.plain_result(result.text)

    @filter.command("更新猜角色图库")
    async def on_update_repo(self, event: AstrMessageEvent):
        """处理 /更新猜角色图库 指令：git pull 拉取最新图片"""
        try:
            await self._ensure_initialized()
        except Exception as e:
            _log.error("[%s] 初始化失败: %s", PLUGIN_NAME, e)
            yield event.plain_result(f"插件初始化失败：{e}")
            return

        result = await handle_update_repo(self._image_repo)
        yield event.plain_result(result)

    @filter.command("重新获取猜角色图库")
    async def on_force_reclone(self, event: AstrMessageEvent):
        """处理 /重新获取猜角色图库 指令：放弃本地修改，从远程强制重新 clone"""
        try:
            await self._ensure_initialized()
        except Exception as e:
            _log.error("[%s] 初始化失败: %s", PLUGIN_NAME, e)
            yield event.plain_result(f"插件初始化失败：{e}")
            return

        result = await handle_force_reclone(self._image_repo)
        yield event.plain_result(result)

    @filter.command("测试所有效果")
    async def on_test_effects(self, event: AstrMessageEvent):
        """处理 /测试所有效果 指令：随机选一张图，对所有效果各应用一次并输出"""
        try:
            await self._ensure_initialized()
        except Exception as e:
            _log.error("[%s] 初始化失败: %s", PLUGIN_NAME, e)
            yield event.plain_result(f"插件初始化失败：{e}")
            return

        if self._image_processor is None or self._image_repo is None:
            yield event.plain_result("插件尚未初始化完成")
            return

        yield event.plain_result("⏳ 正在生成所有效果测试图，请稍候...")

        try:
            image_path, items = await handle_test_effects(
                image_repo=self._image_repo,
                image_processor=self._image_processor,
            )
        except Exception:
            _log.exception("[%s] 生成效果测试图失败", PLUGIN_NAME)
            yield event.plain_result("生成效果测试图失败，请查看日志")
            return

        if image_path is None or not items:
            yield event.plain_result("图片库为空或尚未初始化，无法测试")
            return

        # 过滤出成功的效果
        ok_items = [it for it in items if it.image_bytes]
        if not ok_items:
            yield event.plain_result("所有效果均应用失败，请查看日志")
            return

        is_qq = event.get_platform_name() == "aiocqhttp"

        if is_qq:
            # QQ 平台：合并转发节点输出
            try:
                from astrbot.api.message_components import Node, Nodes

                nodes: list[Node] = []
                # 第一个节点：原图
                try:
                    with open(image_path, "rb") as f:
                        orig_bytes = f.read()
                    nodes.append(
                        Node(
                            uin=event.get_self_id(),
                            name="原图",
                            content=[Image.fromBytes(orig_bytes), Plain("原图")],
                        )
                    )
                except Exception:
                    _log.exception("[%s] 读取原图失败: %s", PLUGIN_NAME, image_path)

                # 后续节点：每个效果一张图 + 标注
                for item in ok_items:
                    meta = item.meta
                    params_text = (
                        ", ".join(f"{k}={v}" for k, v in meta.params.items())
                        if meta.params
                        else "无参数"
                    )
                    caption = f"{meta.display_name}（{meta.key}）\n参数: {params_text}\n启用: {'是' if meta.enabled else '否'}"
                    nodes.append(
                        Node(
                            uin=event.get_self_id(),
                            name="效果测试",
                            content=[Image.fromBytes(item.image_bytes), Plain(caption)],
                        )
                    )

                yield event.chain_result([Nodes(nodes)])
            except Exception:
                _log.exception("[%s] 发送合并转发测试结果失败", PLUGIN_NAME)
                yield event.plain_result("发送测试结果失败，请查看日志")
        else:
            # 其他平台：逐条发送
            try:
                # 原图
                try:
                    with open(image_path, "rb") as f:
                        orig_bytes = f.read()
                    yield event.chain_result(
                        [Image.fromBytes(orig_bytes), Plain("原图")]
                    )
                except Exception:
                    _log.exception("[%s] 读取原图失败: %s", PLUGIN_NAME, image_path)

                # 各效果
                failed = 0
                for item in items:
                    if item.image_bytes is None:
                        failed += 1
                        continue
                    meta = item.meta
                    params_text = (
                        ", ".join(f"{k}={v}" for k, v in meta.params.items())
                        if meta.params
                        else "无参数"
                    )
                    caption = (
                        f"{meta.display_name}（{meta.key}）\n"
                        f"参数: {params_text}\n"
                        f"启用: {'是' if meta.enabled else '否'}"
                    )
                    yield event.chain_result(
                        [Image.fromBytes(item.image_bytes), Plain(caption)]
                    )

                if failed:
                    yield event.plain_result(f"有 {failed} 个效果应用失败，请查看日志")
            except Exception:
                _log.exception("[%s] 发送测试结果失败", PLUGIN_NAME)
                yield event.plain_result("发送测试结果失败，请查看日志")

    # ── 消息监听 ──────────────────────────────

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_all_messages(self, event: AstrMessageEvent):
        """监听所有消息，检测是否为活跃游戏的答案"""
        if self._coordinator is None:
            return

        conversation_id = event.unified_msg_origin

        # 快速判断：是否有任何活跃房间
        if not self._coordinator.has_any_active_room():
            return

        # 命中活跃游戏：阻止默认 LLM 调用，避免 @机器人 时答案消息被 LLM 接管
        event.should_call_llm(True)

        # 获取消息文本
        message_text = self._get_message_text(event)
        if not message_text:
            return

        sender_id = self._get_sender_id(event)
        sender_name = self._get_sender_name(event)

        try:
            result = await handle_answer(
                coordinator=self._coordinator,
                conversation_id=conversation_id,
                message_text=message_text,
                answerer_id=sender_id,
                answerer_name=sender_name,
            )
        except Exception:
            _log.exception("[%s] 处理答案时发生异常", PLUGIN_NAME)
            return

        if result is not None:
            text, image_path = result
            # 答对后发送文字 + 原图
            try:
                yield event.chain_result(
                    [Plain(text + "\n"), Image.fromFileSystem(image_path)]
                )
            except Exception:
                _log.exception(
                    "[%s] 发送答对原图失败: image_path=%s", PLUGIN_NAME, image_path
                )
                # 原图发送失败时至少把文字发出去
                yield event.plain_result(text)

    # ── 事件信息提取 ──────────────────────────

    def _get_sender_id(self, event: AstrMessageEvent) -> str:
        """安全提取发送者 ID"""
        try:
            return str(event.get_sender_id())
        except (AttributeError, TypeError):
            pass
        try:
            return str(event.message_obj.sender.user_id)
        except AttributeError:
            pass
        return "unknown"

    def _get_sender_name(self, event: AstrMessageEvent) -> str:
        """安全提取发送者昵称"""
        try:
            return str(event.get_sender_name())
        except (AttributeError, TypeError):
            pass
        try:
            return str(event.message_obj.sender.nickname)
        except AttributeError:
            pass
        return "玩家"

    def _get_message_text(self, event: AstrMessageEvent) -> str:
        """安全提取消息文本"""
        try:
            return event.message_str
        except AttributeError:
            pass
        try:
            return event.get_message_str()
        except (AttributeError, TypeError):
            pass
        return ""

    # ── 生命周期 ──────────────────────────────

    async def terminate(self) -> None:
        """插件卸载时清理资源"""
        if self._coordinator is not None:
            await self._coordinator.shutdown()
        if self._storage is not None:
            await self._storage.close()
        _log.info("[%s] 插件已卸载", PLUGIN_NAME)
