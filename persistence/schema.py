"""
数据表结构定义

所有表名和字段名在此集中管理，
业务代码不应硬编码任何 SQL 标识符
"""

from typing import Final

# ──────────────────────────────────────────────
# 表名
# ──────────────────────────────────────────────

TABLE_PARTICIPANT_METRICS: Final[str] = "participant_metrics"

# ──────────────────────────────────────────────
# participant_metrics 字段
# ──────────────────────────────────────────────

COL_ACCOUNT_ID: Final[str] = "account_id"
COL_DISPLAY_NAME: Final[str] = "display_name"
COL_ATTEMPTS_TOTAL: Final[str] = "attempts_total"
COL_SUCCESS_TOTAL: Final[str] = "success_total"
COL_QUOTA_DATE: Final[str] = "quota_date"
COL_QUOTA_USED: Final[str] = "quota_used"

# ──────────────────────────────────────────────
# 建表 SQL
# ──────────────────────────────────────────────

DDL_CREATE_PARTICIPANT_METRICS: Final[str] = f"""
CREATE TABLE IF NOT EXISTS {TABLE_PARTICIPANT_METRICS} (
    {COL_ACCOUNT_ID}      TEXT PRIMARY KEY,
    {COL_DISPLAY_NAME}    TEXT NOT NULL DEFAULT '',
    {COL_ATTEMPTS_TOTAL}  INTEGER NOT NULL DEFAULT 0,
    {COL_SUCCESS_TOTAL}   INTEGER NOT NULL DEFAULT 0,
    {COL_QUOTA_DATE}      TEXT NOT NULL DEFAULT '',
    {COL_QUOTA_USED}      INTEGER NOT NULL DEFAULT 0
);
"""

# ──────────────────────────────────────────────
# 字段描述表 —— 用于自动补充缺失字段
# 每个条目: (列名, 列定义 SQL 片段)
# ──────────────────────────────────────────────

REQUIRED_COLUMNS: Final[list[tuple[str, str]]] = [
    (COL_ACCOUNT_ID, "TEXT PRIMARY KEY"),
    (COL_DISPLAY_NAME, "TEXT NOT NULL DEFAULT ''"),
    (COL_ATTEMPTS_TOTAL, "INTEGER NOT NULL DEFAULT 0"),
    (COL_SUCCESS_TOTAL, "INTEGER NOT NULL DEFAULT 0"),
    (COL_QUOTA_DATE, "TEXT NOT NULL DEFAULT ''"),
    (COL_QUOTA_USED, "INTEGER NOT NULL DEFAULT 0"),
]
