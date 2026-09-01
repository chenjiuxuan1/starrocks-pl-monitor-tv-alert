#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统计 StarRocks 数仓与财务库数据一致性校验表记录数并发送 TV 告警（财务段）。

数仓与财务库数据一致性校验：
   统计对账 SQL 中 table_name 以 ods_security. 开头的记录（cw_catalog.capital.bi_* 三表对账）
     - cw_catalog.capital.bi_collection_report vs ods_security.ods_capital_bi_collection_report
     - cw_catalog.capital.bi_report_apportion_before vs ods_security.ods_capital_bi_report_apportion_before
     - cw_catalog.capital.bi_report_apportion_after vs ods_security.ods_capital_bi_report_apportion_after
   异常规则: 只统计 abs(diff) > 1 的记录（绝对值过滤科学计数法浮点噪声，如 5e-7）

真实密码请通过环境变量传入:
    SR_PASSWORD=... python3 alert/fin_manage_ods_data_quality_monitor_alert.py

也可以通过命令行参数传入:
    python3 alert/fin_manage_ods_data_quality_monitor_alert.py --sr-password '...'
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
    "FIN_MANAGE_ODS_DATA_QUALITY_TV_BOT_ID",
    "f82292a5-45c5-42ea-84da-272b4c81ebcc",
)
DEFAULT_MENTIONS = [
    item.strip()
    for item in os.environ.get(
        "FIN_MANAGE_ODS_DATA_QUALITY_TV_MENTIONS",
        "adamyu@kn.group,gretchenhe@kn.group",
    ).split(",")
    if item.strip()
]

# ---------- 对账 SQL（与用户提供的校验语句一致，仅去掉首行 `set @v_start_date`，直接写 current_date()） ----------
# 阈值：异常只统计 abs(diff) > 1 的记录（绝对值过滤科学计数法浮点噪声，如 5e-7）
DIFF_THRESHOLD = 1

# 财务库对账：capital 三表（table_name 以 ods_security. 开头）
FIN_UNION_SELECT = r"""
    select
        current_date() as dt
        ,'ods_security.ods_capital_bi_collection_report' as table_name
        ,a.bi_collection_report_id as mysql_primary_key
        ,a.bi_collection_report_total_amount as src_value
        ,coalesce(b.bi_collection_report_total_amount, 0) as dest_value
        ,a.bi_collection_report_total_amount - coalesce(b.bi_collection_report_total_amount, 0) as diff
    from cw_catalog.capital.bi_collection_report a
    left join ods_security.ods_capital_bi_collection_report b
      on b.bi_collection_report_id = a.bi_collection_report_id

    union all
    select
        current_date() as dt
        ,'ods_security.ods_capital_bi_report_apportion_before' as table_name
        ,a.bi_report_apportion_before_id as mysql_primary_key
        ,a.bi_report_apportion_before_amount as src_value
        ,coalesce(b.bi_report_apportion_before_amount, 0) as dest_value
        ,a.bi_report_apportion_before_amount - coalesce(b.bi_report_apportion_before_amount, 0) as diff
    from cw_catalog.capital.bi_report_apportion_before a
    left join ods_security.ods_capital_bi_report_apportion_before b
      on b.bi_report_apportion_before_id = a.bi_report_apportion_before_id

    union all
    select
        current_date() as dt
        ,'ods_security.ods_capital_bi_report_apportion_after' as table_name
        ,a.bi_report_apportion_after_id as mysql_primary_key
        ,a.bi_report_apportion_after_amount as src_value
        ,coalesce(b.bi_report_apportion_after_amount, 0) as dest_value
        ,a.bi_report_apportion_after_amount - coalesce(b.bi_report_apportion_after_amount, 0) as diff
    from cw_catalog.capital.bi_report_apportion_after a
    left join ods_security.ods_capital_bi_report_apportion_after b
      on b.bi_report_apportion_after_id = a.bi_report_apportion_after_id
"""

# ---------- 财务库告警（对账 SQL 中 ods_security. 开头的记录，即 capital 三表） ----------
FIN_MONITOR_TABLE = "ods_security vs cw_catalog"
# 总记录数：全部 capital 对账行
LATEST_BATCH_TOTAL_COUNT_SQL = (
    f"select count(1) as alert_count from ({FIN_UNION_SELECT}) __t"
)
# 异常记录数：只统计 abs(diff) > 1 的记录（绝对值过滤 5e-7 这类科学计数法浮点噪声）
LATEST_BATCH_EXCEPTION_COUNT_SQL = (
    f"select count(1) as alert_count from ({FIN_UNION_SELECT}) __t "
    f"where abs(__t.diff) > {DIFF_THRESHOLD}"
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
        default_db="fin",
        default_username="e_load",
        default_backup_username="e_backup",
    )


def _connect_with_account(config, account):
    return sr_client._connect_with_account(config, account)


def get_connection(config=None):
    return sr_client.get_connection(config or get_starrocks_config())


def _count_from_row(row):
    row = row or {}
    if isinstance(row, dict):
        return int(row.get("alert_count") or row.get("count(1)") or 0)
    return int(row[0] or 0)


def _fetch_counts_from_sql(total_sql, exception_sql, config=None):
    conn = get_connection(config=config)
    try:
        cursor = conn.cursor()
        cursor.execute(total_sql)
        alert_count = _count_from_row(cursor.fetchone())
        cursor.execute(exception_sql)
        exception_count = _count_from_row(cursor.fetchone())
        return {
            "alert_count": alert_count,
            "exception_count": exception_count,
        }
    finally:
        conn.close()


def fetch_latest_batch_counts(config=None, sr_password=None, sr_backup_password=None):
    """财务库告警：capital 三表对账记录数 + abs(diff)>1 异常记录数。"""
    if config is None:
        config = get_starrocks_config(
            sr_password=sr_password,
            sr_backup_password=sr_backup_password,
        )
    return _fetch_counts_from_sql(
        LATEST_BATCH_TOTAL_COUNT_SQL,
        LATEST_BATCH_EXCEPTION_COUNT_SQL,
        config=config,
    )


def format_alert_message(alert_count, exception_count, title, monitor_table=FIN_MONITOR_TABLE, mention_labels=None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"🚨 StarRocks {title}",
        "集群: 中国",
        f"告警记录: {alert_count} 条，异常告警：{exception_count}条，",
        f"告警时间: {now}",
        f"查询表: {monitor_table}",
    ]
    return "\n".join(lines)


def format_fin_alert_message(alert_count, exception_count, mention_labels=None):
    return format_alert_message(
        alert_count,
        exception_count,
        title="数仓与财务库数据一致性校验",
        monitor_table=FIN_MONITOR_TABLE,
    )


def send_to_tv(message, mentions=None, bot_id=None, api_url=None):
    # run_alert.py 不传 --mentions 时 mentions 为空列表，需回退到默认提醒人（含 gretchenhe）
    if not mentions:
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
    message = format_fin_alert_message(
        alert_count=counts["alert_count"],
        exception_count=counts["exception_count"],
    )
    if not message.endswith("\n"):
        message = f"{message}\n"

    if dry_run:
        print(message)
        return {"success": True, "status_code": None, "response": "dry_run"}

    # 只发送财务库告警
    result = send_to_tv(message, mentions=mentions, bot_id=bot_id)
    if result["success"]:
        print(f"✅ TV告警发送成功（财务库）(HTTP {result['status_code']})")
    else:
        print(f"❌ TV告警发送失败 (HTTP {result['status_code']})")
        print(result["response"])
    return result


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="统计 StarRocks 数仓与财务库数据一致性校验记录数并发送 TV 告警（财务段）")
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
