#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统计 StarRocks PL 监控最新批次记录数并发送 TV 告警。

默认查询:
    select count(1) from fin_global.manage_model_global_pl_monitor
    where etl_create_time = (
        select max(etl_create_time) from fin_global.manage_model_global_pl_monitor
    )

真实密码请通过环境变量传入:
    SR_PASSWORD=... python3 alert/manage_model_global_pl_monitor_alert.py

也可以通过命令行参数传入:
    python3 alert/manage_model_global_pl_monitor_alert.py --sr-password '...'
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alert.common import sr_client, tv_sender

pymysql = sr_client.pymysql
urllib = tv_sender.urllib

try:
    from config import auto_load_env  # noqa: F401
except Exception:
    auto_load_env = None


TV_API_URL = os.environ.get(
    "TV_API_URL",
    "https://tv-service-alert.kuainiu.chat/alert/v2/array",
)
TV_BOT_ID = os.environ.get(
    "MANAGE_MODEL_GLOBAL_PL_TV_BOT_ID",
    "f82292a5-45c5-42ea-84da-272b4c81ebcc",
)
DEFAULT_MENTIONS = [
    item.strip()
    for item in os.environ.get(
        "MANAGE_MODEL_GLOBAL_PL_TV_MENTIONS",
        "adamyu@kn.group,gretchenhe@kn.group",
    ).split(",")
    if item.strip()
]

MONITOR_TABLE = "fin_global.manage_model_global_pl_monitor"
CONSISTENCY_MONITOR_URL = "https://data.kuainiu.io/collection/2632-pl"
VOLATILITY_MONITOR_URL = "https://data.kuainiu.io/dashboard/2241-v3"
LATEST_HOUR_COUNT_SQL = (
    f"select count(1) as alert_count from {MONITOR_TABLE} "
    f"where current_hour = (select max(current_hour) from {MONITOR_TABLE})"
)
LATEST_BATCH_TOTAL_COUNT_SQL = (
    f"select count(1) as alert_count from {MONITOR_TABLE} "
    f"where etl_create_time = (select max(etl_create_time) from {MONITOR_TABLE})"
)
LATEST_BATCH_EXCEPTION_COUNT_SQL = (
    f"select count(1) as alert_count from {MONITOR_TABLE} "
    f"where etl_create_time = (select max(etl_create_time) from {MONITOR_TABLE}) "
    "and abs(diff) > 1"
)
DEFAULT_LIMIT = 1


StarRocksAccount = sr_client.StarRocksAccount
StarRocksConfig = sr_client.StarRocksConfig


def get_starrocks_config(sr_password=None, sr_backup_password=None):
    return sr_client.build_config(
        sr_password=sr_password,
        sr_backup_password=sr_backup_password,
        default_host="nlb-ngj6e0efsvv7wm73v3.cn-shanghai.nlb.aliyuncsslb.com",
        default_port=9031,
        default_db="ods",
        default_username="e_load",
        default_backup_username="e_backup",
    )


def _connect_with_account(config, account):
    return sr_client._connect_with_account(config, account)


def get_connection(config=None):
    return sr_client.get_connection(config or get_starrocks_config())


def fetch_random_rows(limit=DEFAULT_LIMIT, config=None, sr_password=None, sr_backup_password=None):
    safe_limit = max(1, int(limit))
    sql = f"SELECT * FROM {MONITOR_TABLE} ORDER BY RAND() LIMIT {safe_limit}"
    if config is None:
        config = get_starrocks_config(
            sr_password=sr_password,
            sr_backup_password=sr_backup_password,
        )
    conn = get_connection(config=config)
    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        return list(cursor.fetchall())
    finally:
        conn.close()


def fetch_latest_hour_count(config=None, sr_password=None, sr_backup_password=None):
    if config is None:
        config = get_starrocks_config(
            sr_password=sr_password,
            sr_backup_password=sr_backup_password,
        )
    conn = get_connection(config=config)
    try:
        cursor = conn.cursor()
        cursor.execute(LATEST_HOUR_COUNT_SQL)
        row = cursor.fetchone() or {}
        if isinstance(row, dict):
            return int(row.get("alert_count") or row.get("count(1)") or 0)
        return int(row[0] or 0)
    finally:
        conn.close()


def _count_from_row(row):
    row = row or {}
    if isinstance(row, dict):
        return int(row.get("alert_count") or row.get("count(1)") or 0)
    return int(row[0] or 0)


def fetch_latest_batch_counts(config=None, sr_password=None, sr_backup_password=None):
    if config is None:
        config = get_starrocks_config(
            sr_password=sr_password,
            sr_backup_password=sr_backup_password,
        )
    conn = get_connection(config=config)
    try:
        cursor = conn.cursor()
        cursor.execute(LATEST_BATCH_TOTAL_COUNT_SQL)
        alert_count = _count_from_row(cursor.fetchone())
        cursor.execute(LATEST_BATCH_EXCEPTION_COUNT_SQL)
        exception_count = _count_from_row(cursor.fetchone())
        return {
            "alert_count": alert_count,
            "exception_count": exception_count,
        }
    finally:
        conn.close()


def _stringify(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _get(row, *names):
    lowered = {str(key).lower(): value for key, value in row.items()}
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return value
    return None


def _append_known_line(lines, row, label, *names):
    value = _get(row, *names)
    if value not in (None, ""):
        lines.append(f"• {label}: {_stringify(value)}")
        return True
    return False


def _format_row(row, index):
    lines = [f"【告警记录 {index}】"]
    used = set()
    known_fields = [
        ("开始时间", ("start_time", "query_start_time", "starttime", "startTime")),
        ("查询ID", ("query_id", "queryid", "queryId")),
        ("连接ID", ("conn_id", "connection_id", "connid", "connectionId")),
        ("数据库", ("db", "database", "database_name", "db_name")),
        ("用户", ("user", "username", "user_name")),
        ("扫描字节", ("scan_bytes", "scan_bytes_human", "scanBytes")),
        ("扫描行数", ("scan_rows", "scanRows", "scan_row_count")),
        ("内存使用", ("mem_usage", "memory_usage", "memUsage", "memory")),
        ("CPU时间", ("cpu_time", "cpuTime", "cpu_cost")),
        ("执行时间", ("exec_time", "execute_time", "query_time", "duration")),
        ("仓库", ("warehouse", "warehouse_name")),
        ("资源组", ("resource_group", "resource_group_name", "resourceGroup")),
        ("SQL", ("sql", "sql_text", "stmt", "statement")),
    ]

    lowered_to_original = {str(key).lower(): key for key in row}
    for label, names in known_fields:
        if _append_known_line(lines, row, label, *names):
            for name in names:
                original = lowered_to_original.get(name.lower())
                if original:
                    used.add(original)

    query_id = _get(row, "query_id", "queryid", "queryId")
    if query_id:
        lines.append(f"• SQL详情: https://sr-admin.kuainiujinke.com/queryid/{query_id}")

    extra_items = [
        (key, value)
        for key, value in row.items()
        if key not in used and value not in (None, "")
    ]
    if extra_items:
        lines.append("• 其他字段:")
        for key, value in extra_items:
            lines.append(f"  - {key}: {_stringify(value)}")

    return "\n".join(lines)


def format_alert_message(alert_count, exception_count, mention_labels=None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "🚨 StarRocks PL监控告警",
        "集群: 中国",
        f"告警记录: {alert_count} 条，异常告警：{exception_count}条，",
        f"告警时间: {now}",
        f"数据一致性监控:{CONSISTENCY_MONITOR_URL}",
        f"数据波动监控:{VOLATILITY_MONITOR_URL}",
    ]
    return "\n".join(lines)


def send_to_tv(message, mentions=None, bot_id=None, api_url=None):
    if mentions is None:
        mentions = DEFAULT_MENTIONS
    return tv_sender.send_to_tv(
        message,
        mentions=mentions,
        bot_id=bot_id or TV_BOT_ID,
        api_url=api_url or TV_API_URL,
    )


def run(limit=DEFAULT_LIMIT, dry_run=False, mentions=None, sr_password=None, sr_backup_password=None, bot_id=None):
    config = get_starrocks_config(
        sr_password=sr_password,
        sr_backup_password=sr_backup_password,
    )
    counts = fetch_latest_batch_counts(config=config)
    message = format_alert_message(
        alert_count=counts["alert_count"],
        exception_count=counts["exception_count"],
    )
    if not message.endswith("\n"):
        message = f"{message}\n"

    if dry_run:
        print(message)
        return {"success": True, "status_code": None, "response": "dry_run"}

    result = send_to_tv(message, mentions=mentions, bot_id=bot_id)
    if result["success"]:
        print(f"✅ TV告警发送成功 (HTTP {result['status_code']})")
    else:
        print(f"❌ TV告警发送失败 (HTTP {result['status_code']})")
        print(result["response"])
    return result


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="统计 StarRocks PL 监控最新批次记录数并发送 TV 告警")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="兼容旧参数，当前告警不使用")
    parser.add_argument("--dry-run", action="store_true", help="只打印消息，不发送 TV")
    parser.add_argument("--sr-password", default=None, help="StarRocks 主账号密码")
    parser.add_argument("--sr-backup-password", default=None, help="StarRocks 备份账号密码")
    parser.add_argument("--bot-id", default=None, help="指定发送使用的 TV 机器人 ID")
    parser.add_argument(
        "--mentions",
        default=",".join(DEFAULT_MENTIONS),
        help="逗号分隔的提醒邮箱列表",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    mentions = [item.strip() for item in args.mentions.split(",") if item.strip()]
    result = run(
        limit=args.limit,
        dry_run=args.dry_run,
        mentions=mentions,
        sr_password=args.sr_password,
        sr_backup_password=args.sr_backup_password,
        bot_id=args.bot_id,
    )
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
