#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Refresh Indonesia marketing DWD table count check and send TV alert.

The check writes T-1 counts into testdb.test_dwd_ad_table_cnt_check, then alerts
every expected DWD table whose T-1 partition count is zero or missing.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymysql
from pymysql.cursors import DictCursor

try:
    from config import auto_load_env  # noqa: F401
except Exception:
    auto_load_env = None


TV_API_URL = os.environ.get(
    "TV_API_URL",
    "https://tv-service-alert.kuainiu.chat/alert/v2/array",
)
TV_BOT_ID = os.environ.get(
    "ID_MARKETING_DWD_TABLE_CNT_TV_BOT_ID",
    "replace-with-indonesia-marketing-alert-bot-id",
)
DEFAULT_MENTIONS = [
    item.strip()
    for item in os.environ.get(
        "ID_MARKETING_DWD_TABLE_CNT_TV_MENTIONS",
        "adamyu@kn.group,gretchenhe@kn.group",
    ).split(",")
    if item.strip()
]

CHECK_TABLE = "testdb.test_dwd_ad_table_cnt_check"
EXPECTED_TABLES = (
    "dwd_ad_gg_conversion_action",
    "dwd_ad_gg_placement",
    "dwd_ad_platform_info",
    "dwd_ad_platform_report_full",
    "dwd_ad_platform_report_snap",
    "dwd_ad_tt_ad_get",
    "dwd_ad_tt_ad_group_get",
    "dwd_ad_tt_ad_report_age_gender",
    "dwd_ad_tt_advertiser_get",
    "dwd_ad_tt_audience_get",
    "dwd_ad_tt_audience_list",
    "dwd_ad_tt_campaign_get",
    "dwd_ad_tt_report",
)

CREATE_CHECK_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {CHECK_TABLE} (
    dt DATE COMMENT 'Data date',
    table_name VARCHAR(128) COMMENT 'DWD table name',
    cnt BIGINT COMMENT 'T-1 partition row count',
    check_time DATETIME COMMENT 'Check time'
)
DUPLICATE KEY(dt, table_name)
DISTRIBUTED BY HASH(table_name) BUCKETS 4
PROPERTIES (
    "replication_num" = "1"
)
"""


@dataclass
class StarRocksAccount:
    username: str
    password: str


@dataclass
class StarRocksConfig:
    host: str
    port: int
    db: str
    primary: StarRocksAccount
    backup: StarRocksAccount


def get_starrocks_config(sr_password=None, sr_backup_password=None):
    return StarRocksConfig(
        host=os.environ.get("SR_HOST", "127.0.0.1"),
        port=int(os.environ.get("SR_PORT", "9030")),
        db=os.environ.get("SR_DB", "testdb"),
        primary=StarRocksAccount(
            username=os.environ.get("SR_USERNAME", "e_load"),
            password=sr_password or os.environ.get("SR_PASSWORD", ""),
        ),
        backup=StarRocksAccount(
            username=os.environ.get("SR_BACKUP_USERNAME", "backup_user"),
            password=sr_backup_password or os.environ.get("SR_BACKUP_PASSWORD", ""),
        ),
    )


def _connect_with_account(config, account):
    return pymysql.connect(
        host=config.host,
        port=config.port,
        user=account.username,
        password=account.password,
        database=config.db,
        charset="utf8mb4",
        cursorclass=DictCursor,
    )


def get_connection(config=None):
    config = config or get_starrocks_config()
    errors = []
    for label, account in (("primary", config.primary), ("backup", config.backup)):
        if not account.password:
            errors.append(f"{label} account {account.username} missing password")
            continue
        try:
            return _connect_with_account(config, account)
        except Exception as exc:
            errors.append(f"{label} account {account.username} failed: {exc}")
    raise RuntimeError("StarRocks connection failed: " + "; ".join(errors))


def default_target_date():
    return date.today() - timedelta(days=1)


def parse_date(value):
    if value is None or isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


def _quote_date(value):
    return parse_date(value).strftime("%Y-%m-%d")


def build_refresh_sql(target_date):
    dt = _quote_date(target_date)
    parts = [
        (
            f"select '{table_name}' as table_name, count(1) as cnt "
            f"from dwd.{table_name} where dt = '{dt}'"
        )
        for table_name in EXPECTED_TABLES
    ]
    return (
        f"INSERT OVERWRITE {CHECK_TABLE}\n"
        "SELECT\n"
        f"    '{dt}' AS dt,\n"
        "    table_name,\n"
        "    cnt,\n"
        "    now() AS check_time\n"
        "FROM (\n    "
        + "\n    union all\n    ".join(parts)
        + "\n) t"
    )


def refresh_check_table(target_date=None, config=None, sr_password=None, sr_backup_password=None):
    target_date = parse_date(target_date) or default_target_date()
    if config is None:
        config = get_starrocks_config(
            sr_password=sr_password,
            sr_backup_password=sr_backup_password,
        )
    conn = get_connection(config=config)
    try:
        cursor = conn.cursor()
        cursor.execute(CREATE_CHECK_TABLE_SQL)
        cursor.execute(build_refresh_sql(target_date))
    finally:
        conn.close()


def fetch_check_rows(target_date=None, config=None, sr_password=None, sr_backup_password=None):
    target_date = parse_date(target_date) or default_target_date()
    if config is None:
        config = get_starrocks_config(
            sr_password=sr_password,
            sr_backup_password=sr_backup_password,
        )
    sql = (
        f"select dt, table_name, cnt, check_time from {CHECK_TABLE} "
        "where dt = %s order by table_name"
    )
    conn = get_connection(config=config)
    try:
        cursor = conn.cursor()
        cursor.execute(sql, (target_date.strftime("%Y-%m-%d"),))
        return list(cursor.fetchall())
    finally:
        conn.close()


def _to_int(value, default=0):
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def find_problem_tables(rows, expected_tables=EXPECTED_TABLES):
    rows_by_table = {str(row.get("table_name")): row for row in rows}
    problems = []
    for table_name in expected_tables:
        row = rows_by_table.get(table_name)
        if row is None:
            problems.append({"table_name": table_name, "cnt": None, "reason": "missing_check_result"})
            continue
        cnt = _to_int(row.get("cnt"), default=0)
        if cnt <= 0:
            problems.append({"table_name": table_name, "cnt": cnt, "reason": "zero_count"})
    return problems


def _format_datetime(value):
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value or "")


def _format_count(value):
    if value is None:
        return "未查询到"
    return f"{_to_int(value):,}"


def format_alert_message(rows, target_date=None, problems=None):
    target_date = parse_date(target_date) or default_target_date()
    problems = find_problem_tables(rows) if problems is None else problems
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    latest_check_time = ""
    for row in rows:
        check_time = _format_datetime(row.get("check_time"))
        if check_time > latest_check_time:
            latest_check_time = check_time

    lines = [
        "🚨 印尼投放DWD表T-1产出校验",
        f"告警时间: {now}",
        f"统计日期: {target_date.strftime('%Y-%m-%d')}",
        f"校验表: {CHECK_TABLE}",
        f"预期表数: {len(EXPECTED_TABLES)}，实际校验结果: {len(rows)}，异常表数: {len(problems)}",
    ]
    if latest_check_time:
        lines.append(f"最近校验时间: {latest_check_time}")

    if not problems:
        lines.append("异常明细: 无，所有投放DWD表T-1分区均有数据")
        return "\n".join(lines)

    lines.append("")
    lines.append("异常明细:")
    rows_by_table = {str(row.get("table_name")): row for row in rows}
    for index, problem in enumerate(problems, start=1):
        table_name = problem["table_name"]
        row = rows_by_table.get(table_name, {})
        cnt = problem.get("cnt", row.get("cnt"))
        reason = (
            f"{target_date.strftime('%Y-%m-%d')} 校验结果缺失，数据有问题"
            if problem.get("reason") == "missing_check_result"
            else f"{target_date.strftime('%Y-%m-%d')} 数据量为0，数据有问题"
        )
        lines.append(f"{index}. {table_name} | cnt={_format_count(cnt)} | {reason}")
    return "\n".join(lines)


def send_to_tv(message, mentions=None, bot_id=None, api_url=None):
    if mentions is None:
        mentions = DEFAULT_MENTIONS
    if not message.endswith("\n"):
        message = f"{message}\n"

    payload = {
        "botId": bot_id or TV_BOT_ID,
        "message": message,
        "mentions": mentions,
    }
    json_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        api_url or TV_API_URL,
        data=json_data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status_code = response.getcode()
            response_body = response.read().decode("utf-8")
            return {
                "success": 200 <= status_code < 300,
                "status_code": status_code,
                "response": response_body,
            }
    except urllib.error.HTTPError as exc:
        response_body = ""
        if exc.fp is not None:
            try:
                response_body = exc.fp.read().decode("utf-8")
            except Exception:
                response_body = ""
        return {
            "success": False,
            "status_code": exc.code,
            "response": response_body or str(exc.reason),
        }
    except Exception as exc:
        return {
            "success": False,
            "status_code": None,
            "response": str(exc),
        }


def run(dry_run=False, mentions=None, sr_password=None, sr_backup_password=None, bot_id=None, target_date=None, skip_refresh=False):
    target_date = parse_date(target_date) or default_target_date()
    config = get_starrocks_config(
        sr_password=sr_password,
        sr_backup_password=sr_backup_password,
    )
    if not skip_refresh:
        refresh_check_table(target_date=target_date, config=config)
    rows = fetch_check_rows(target_date=target_date, config=config)
    message = format_alert_message(rows, target_date=target_date)
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
    parser = argparse.ArgumentParser(description="刷新并告警印尼投放 DWD 表 T-1 数据量校验")
    parser.add_argument("--dry-run", action="store_true", help="只打印消息，不发送 TV")
    parser.add_argument("--target-date", default=None, help="指定统计日，格式 YYYY-MM-DD；默认 T-1")
    parser.add_argument("--skip-refresh", action="store_true", help="跳过建表和 INSERT OVERWRITE，只读取校验表")
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
        dry_run=args.dry_run,
        mentions=mentions,
        sr_password=args.sr_password,
        sr_backup_password=args.sr_backup_password,
        bot_id=args.bot_id,
        target_date=parse_date(args.target_date),
        skip_refresh=args.skip_refresh,
    )
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
