"""
SQLite 存储层

职责：
  - 数据库连接管理（单例）
  - 自动建表 + 自动补充缺失字段
  - 参与者统计数据的读写

线程安全说明：
  - 使用 aiosqlite 保证异步安全
  - 所有公共方法均为 async
"""

from __future__ import annotations

import aiosqlite
from pathlib import Path
from typing import Optional

from .schema import (
    DDL_CREATE_PARTICIPANT_METRICS,
    REQUIRED_COLUMNS,
    TABLE_PARTICIPANT_METRICS,
    COL_ACCOUNT_ID,
    COL_DISPLAY_NAME,
    COL_ATTEMPTS_TOTAL,
    COL_SUCCESS_TOTAL,
    COL_QUOTA_DATE,
    COL_QUOTA_USED,
)


class ParticipantRecord:
    """单条参与者统计记录的只读视图"""

    __slots__ = (
        "account_id",
        "display_name",
        "attempts_total",
        "success_total",
        "quota_date",
        "quota_used",
    )

    def __init__(
        self,
        account_id: str,
        display_name: str,
        attempts_total: int,
        success_total: int,
        quota_date: str,
        quota_used: int,
    ) -> None:
        self.account_id = account_id
        self.display_name = display_name
        self.attempts_total = attempts_total
        self.success_total = success_total
        self.quota_date = quota_date
        self.quota_used = quota_used


class MetricsStorage:
    """
    封装 participant_metrics 表的全部读写操作

    使用方式：
        storage = MetricsStorage(db_path)
        await storage.initialize()
        record = await storage.get_record(account_id)
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path: Path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    # ── 生命周期 ──────────────────────────────

    async def initialize(self) -> None:
        """打开连接、建表、补字段插件启动时调用一次"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._db_path))
        # 开启 WAL 模式，提升并发读性能
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._create_tables()
        await self._ensure_columns()

    async def close(self) -> None:
        """关闭连接插件卸载时调用"""
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ── 建表 & 补字段 ─────────────────────────

    async def _create_tables(self) -> None:
        """执行建表（IF NOT EXISTS，安全重复调用）"""
        await self._db.execute(DDL_CREATE_PARTICIPANT_METRICS)
        await self._db.commit()

    async def _ensure_columns(self) -> None:
        """
        检测已有列，对缺失列执行 ALTER TABLE ADD COLUMN
        保证版本升级后新增字段自动生效
        """
        cursor = await self._db.execute(
            f"PRAGMA table_info({TABLE_PARTICIPANT_METRICS});"
        )
        existing_columns: set[str] = set()
        async for row in cursor:
            # row[1] 是列名
            existing_columns.add(row[1])

        for col_name, col_def in REQUIRED_COLUMNS:
            if col_name not in existing_columns:
                alter_sql = (
                    f"ALTER TABLE {TABLE_PARTICIPANT_METRICS} "
                    f"ADD COLUMN {col_name} {col_def};"
                )
                await self._db.execute(alter_sql)

        await self._db.commit()

    # ── 查询 ──────────────────────────────────

    async def get_record(self, account_id: str) -> Optional[ParticipantRecord]:
        """按 account_id 查询记录，不存在返回 None"""
        sql = f"""
            SELECT
                {COL_ACCOUNT_ID},
                {COL_DISPLAY_NAME},
                {COL_ATTEMPTS_TOTAL},
                {COL_SUCCESS_TOTAL},
                {COL_QUOTA_DATE},
                {COL_QUOTA_USED}
            FROM {TABLE_PARTICIPANT_METRICS}
            WHERE {COL_ACCOUNT_ID} = ?;
        """
        cursor = await self._db.execute(sql, (account_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        return ParticipantRecord(
            account_id=row[0],
            display_name=row[1],
            attempts_total=row[2],
            success_total=row[3],
            quota_date=row[4],
            quota_used=row[5],
        )

    # ── 每日额度判定 ──────────────────────────

    async def get_effective_quota_used(self, account_id: str, today: str) -> int:
        """
        获取当日已使用次数

        如果 quota_date != today，视为新的一天，返回 0
        不依赖定时任务清零
        """
        record = await self.get_record(account_id)
        if record is None:
            return 0
        if record.quota_date != today:
            return 0
        return record.quota_used

    # ── 写入 ──────────────────────────────────

    async def record_attempt(
        self,
        account_id: str,
        display_name: str,
        today: str,
    ) -> None:
        """
        记录一次游戏尝试：
          - attempts_total += 1
          - quota_used += 1（若跨日则重置为 1）
          - 更新 display_name 和 quota_date
        """
        existing = await self.get_record(account_id)

        if existing is None:
            # 全新用户
            sql = f"""
                INSERT INTO {TABLE_PARTICIPANT_METRICS}
                    ({COL_ACCOUNT_ID},
                     {COL_DISPLAY_NAME},
                     {COL_ATTEMPTS_TOTAL},
                     {COL_SUCCESS_TOTAL},
                     {COL_QUOTA_DATE},
                     {COL_QUOTA_USED})
                VALUES (?, ?, 1, 0, ?, 1);
            """
            await self._db.execute(sql, (account_id, display_name, today))
        else:
            # 判断是否跨日
            if existing.quota_date != today:
                new_quota_used = 1
            else:
                new_quota_used = existing.quota_used + 1

            sql = f"""
                UPDATE {TABLE_PARTICIPANT_METRICS}
                SET
                    {COL_DISPLAY_NAME}   = ?,
                    {COL_ATTEMPTS_TOTAL} = {COL_ATTEMPTS_TOTAL} + 1,
                    {COL_QUOTA_DATE}     = ?,
                    {COL_QUOTA_USED}     = ?
                WHERE {COL_ACCOUNT_ID} = ?;
            """
            await self._db.execute(
                sql, (display_name, today, new_quota_used, account_id)
            )

        await self._db.commit()

    async def record_success(self, account_id: str) -> None:
        """
        记录一次猜对：success_total += 1
        仅在已有记录时更新
        """
        sql = f"""
            UPDATE {TABLE_PARTICIPANT_METRICS}
            SET {COL_SUCCESS_TOTAL} = {COL_SUCCESS_TOTAL} + 1
            WHERE {COL_ACCOUNT_ID} = ?;
        """
        await self._db.execute(sql, (account_id,))
        await self._db.commit()
