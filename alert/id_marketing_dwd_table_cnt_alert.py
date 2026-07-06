#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Refresh Indonesia marketing DWD table count check and send TV alert.

The check writes T-1 counts into testdb.test_dwd_ad_table_cnt_check, then alerts
every expected DWD table whose T-1 partition count is zero or missing.
"""

import argparse
import os
import re
import sys
from datetime import date, datetime, timedelta
from dataclasses import dataclass
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

DEFAULT_PROFILE = "id"
DEFAULT_COUNTRY_NAME = "印尼"
DEFAULT_PLATFORM_TYPE = "投放"
DEFAULT_CHECK_TABLE = "testdb.test_dwd_ad_table_cnt_check"
CHECK_TABLE = DEFAULT_CHECK_TABLE
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")
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
MX_EXPECTED_TABLES = (
    "dwd_ad_gg_campaign_unique_users",
    "dwd_ad_gg_conversion_action",
    "dwd_ad_gg_placement",
    "dwd_ad_group_snapshot_h",
    "dwd_ad_insight_snapahot_h",
    "dwd_ad_platform_info",
    "dwd_ad_report_snapshot_h",
    "dwd_ad_tt_ad_get",
    "dwd_ad_tt_ad_group_get",
    "dwd_ad_tt_ad_report_age_gender",
    "dwd_ad_tt_advertiser_get",
    "dwd_ad_tt_audience_get",
    "dwd_ad_tt_audience_list",
    "dwd_ad_tt_campaign_get",
    "dwd_ad_tt_report",
    "dwd_ad_tt_report_placement",
)

MARKETING_DWD_PROFILES = {
    "id": {"country_code": "id", "country_name": "印尼", "platform_type": "投放", "check_table": DEFAULT_CHECK_TABLE, "table_names": EXPECTED_TABLES},
    "ine": {"country_code": "id", "country_name": "印尼", "platform_type": "投放", "check_table": DEFAULT_CHECK_TABLE, "table_names": EXPECTED_TABLES},
    "indonesia": {"country_code": "id", "country_name": "印尼", "platform_type": "投放", "check_table": DEFAULT_CHECK_TABLE, "table_names": EXPECTED_TABLES},
    "ph": {"country_code": "ph", "country_name": "菲律宾", "platform_type": "投放", "check_table": DEFAULT_CHECK_TABLE, "table_names": EXPECTED_TABLES},
    "philippines": {"country_code": "ph", "country_name": "菲律宾", "platform_type": "投放", "check_table": DEFAULT_CHECK_TABLE, "table_names": EXPECTED_TABLES},
    "th": {"country_code": "th", "country_name": "泰国", "platform_type": "投放", "check_table": DEFAULT_CHECK_TABLE, "table_names": EXPECTED_TABLES},
    "thailand": {"country_code": "th", "country_name": "泰国", "platform_type": "投放", "check_table": DEFAULT_CHECK_TABLE, "table_names": EXPECTED_TABLES},
    "mx": {"country_code": "mx", "country_name": "墨西哥", "platform_type": "投放", "check_table": DEFAULT_CHECK_TABLE, "table_names": MX_EXPECTED_TABLES},
    "mexico": {"country_code": "mx", "country_name": "墨西哥", "platform_type": "投放", "check_table": DEFAULT_CHECK_TABLE, "table_names": MX_EXPECTED_TABLES},
    "pk": {"country_code": "pk", "country_name": "巴基斯坦", "platform_type": "投放", "check_table": DEFAULT_CHECK_TABLE, "table_names": EXPECTED_TABLES},
    "pakistan": {"country_code": "pk", "country_name": "巴基斯坦", "platform_type": "投放", "check_table": DEFAULT_CHECK_TABLE, "table_names": EXPECTED_TABLES},
}


@dataclass(frozen=True)
class MarketingDwdCheckConfig:
    profile: str = DEFAULT_PROFILE
    country_code: str = DEFAULT_PROFILE
    country_name: str = DEFAULT_COUNTRY_NAME
    platform_type: str = DEFAULT_PLATFORM_TYPE
    check_table: str = DEFAULT_CHECK_TABLE
    expected_tables: tuple = EXPECTED_TABLES

    @property
    def alert_title(self):
        return f"🚨 {self.country_name}{self.platform_type}DWD表T-1产出校验"


def parse_table_names(value):
    if value is None:
        return EXPECTED_TABLES
    if isinstance(value, (tuple, list)):
        return tuple(item.strip() for item in value if str(item).strip())
    return tuple(item.strip() for item in str(value).split(",") if item.strip())


def validate_identifier(value, field_name):
    if not IDENTIFIER_PATTERN.match(value or ""):
        raise ValueError(f"{field_name} must be a table identifier like table_name or db.table_name: {value}")
    return value


def get_profile_config(profile=None):
    profile = (profile or os.environ.get("MARKETING_DWD_PROFILE", DEFAULT_PROFILE)).strip().lower()
    if profile not in MARKETING_DWD_PROFILES:
        available = ", ".join(sorted(MARKETING_DWD_PROFILES))
        raise ValueError(f"unknown marketing DWD profile: {profile}; available profiles: {available}")
    profile_config = dict(MARKETING_DWD_PROFILES[profile])
    profile_config["profile"] = profile
    return profile_config


def build_check_config(profile=None, country_name=None, platform_type=None, check_table=None, table_names=None):
    profile_config = get_profile_config(profile)
    resolved_country_code = profile_config["country_code"]
    resolved_country_name = country_name or os.environ.get("MARKETING_DWD_COUNTRY_NAME") or profile_config["country_name"]
    resolved_platform_type = platform_type or os.environ.get("MARKETING_DWD_PLATFORM_TYPE") or profile_config["platform_type"]
    resolved_check_table = check_table or os.environ.get("MARKETING_DWD_CHECK_TABLE") or profile_config["check_table"]
    resolved_tables = parse_table_names(table_names or os.environ.get("MARKETING_DWD_TABLE_NAMES") or profile_config["table_names"])
    validate_identifier(resolved_check_table, "check_table")
    for table_name in resolved_tables:
        validate_identifier(table_name, "table_name")
    return MarketingDwdCheckConfig(
        profile=profile_config["profile"],
        country_code=resolved_country_code,
        country_name=resolved_country_name,
        platform_type=resolved_platform_type,
        check_table=resolved_check_table,
        expected_tables=resolved_tables,
    )


def build_create_check_table_sql(check_table=DEFAULT_CHECK_TABLE):
    return f"""
CREATE TABLE IF NOT EXISTS {check_table} (
    dt DATE COMMENT 'Data date',
    country_code VARCHAR(32) COMMENT 'Country code',
    platform_type VARCHAR(64) COMMENT 'Platform type',
    table_name VARCHAR(128) COMMENT 'DWD table name',
    country_name VARCHAR(64) COMMENT 'Country name',
    cnt BIGINT COMMENT 'T-1 partition row count',
    check_time DATETIME COMMENT 'Check time'
)
DUPLICATE KEY(dt, country_code, platform_type, table_name)
DISTRIBUTED BY HASH(country_code, table_name) BUCKETS 8
PROPERTIES (
    "replication_num" = "1"
)
"""


CREATE_CHECK_TABLE_SQL = build_create_check_table_sql(DEFAULT_CHECK_TABLE)


StarRocksAccount = sr_client.StarRocksAccount
StarRocksConfig = sr_client.StarRocksConfig


def get_starrocks_config(sr_password=None, sr_backup_password=None):
    return sr_client.build_config(
        sr_password=sr_password,
        sr_backup_password=sr_backup_password,
        default_host="127.0.0.1",
        default_port=9030,
        default_db="testdb",
        default_username="e_load",
        default_backup_username="backup_user",
    )


def _connect_with_account(config, account):
    return sr_client._connect_with_account(config, account)


def get_connection(config=None):
    return sr_client.get_connection(config or get_starrocks_config())


def default_target_date():
    return date.today() - timedelta(days=1)


def parse_date(value):
    if value is None or isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


def _quote_date(value):
    return parse_date(value).strftime("%Y-%m-%d")


def build_refresh_sql(target_date, check_config=None, check_table=None, expected_tables=None):
    check_config = check_config or MarketingDwdCheckConfig(
        check_table=check_table or DEFAULT_CHECK_TABLE,
        expected_tables=tuple(expected_tables or EXPECTED_TABLES),
    )
    dt = _quote_date(target_date)
    parts = [
        (
            f"select '{table_name}' as table_name, count(1) as cnt "
            f"from dwd.{table_name} where dt = '{dt}'"
        )
        for table_name in check_config.expected_tables
    ]
    return (
        f"INSERT INTO {check_config.check_table} "
        "(dt, country_code, platform_type, table_name, country_name, cnt, check_time)\n"
        "SELECT\n"
        f"    '{dt}' AS dt,\n"
        f"    '{check_config.country_code}' AS country_code,\n"
        f"    '{check_config.platform_type}' AS platform_type,\n"
        "    table_name,\n"
        f"    '{check_config.country_name}' AS country_name,\n"
        "    cnt,\n"
        "    now() AS check_time\n"
        "FROM (\n    "
        + "\n    union all\n    ".join(parts)
        + "\n) t"
    )


def build_delete_existing_sql(target_date, check_config):
    dt = _quote_date(target_date)
    return (
        f"DELETE FROM {check_config.check_table} "
        f"WHERE dt = '{dt}' "
        f"AND country_code = '{check_config.country_code}' "
        f"AND platform_type = '{check_config.platform_type}'"
    )


def refresh_check_table(target_date=None, config=None, sr_password=None, sr_backup_password=None, check_config=None):
    target_date = parse_date(target_date) or default_target_date()
    check_config = check_config or build_check_config()
    if config is None:
        config = get_starrocks_config(
            sr_password=sr_password,
            sr_backup_password=sr_backup_password,
        )
    conn = get_connection(config=config)
    try:
        cursor = conn.cursor()
        cursor.execute(build_create_check_table_sql(check_config.check_table))
        cursor.execute(build_delete_existing_sql(target_date, check_config))
        cursor.execute(build_refresh_sql(target_date, check_config=check_config))
    finally:
        conn.close()


def fetch_check_rows(target_date=None, config=None, sr_password=None, sr_backup_password=None, check_config=None):
    target_date = parse_date(target_date) or default_target_date()
    check_config = check_config or build_check_config()
    if config is None:
        config = get_starrocks_config(
            sr_password=sr_password,
            sr_backup_password=sr_backup_password,
        )
    sql = (
        f"select dt, country_code, country_name, platform_type, table_name, cnt, check_time "
        f"from {check_config.check_table} "
        "where dt = %s and country_code = %s and platform_type = %s order by table_name"
    )
    conn = get_connection(config=config)
    try:
        cursor = conn.cursor()
        cursor.execute(sql, (target_date.strftime("%Y-%m-%d"), check_config.country_code, check_config.platform_type))
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


def find_problem_tables(rows, expected_tables=None):
    problems = []
    for row in rows:
        table_name = str(row.get("table_name") or "")
        if not table_name:
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


def format_alert_message(rows, target_date=None, problems=None, check_config=None):
    target_date = parse_date(target_date) or default_target_date()
    check_config = check_config or build_check_config()
    problems = find_problem_tables(rows) if problems is None else problems
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    latest_check_time = ""
    for row in rows:
        check_time = _format_datetime(row.get("check_time"))
        if check_time > latest_check_time:
            latest_check_time = check_time

    lines = [
        check_config.alert_title,
        f"告警时间: {now}",
        f"统计日期: {target_date.strftime('%Y-%m-%d')}",
        f"校验表: {check_config.check_table}",
        f"校验结果表数: {len(rows)}，异常表数: {len(problems)}",
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
        reason = f"{target_date.strftime('%Y-%m-%d')} 数据量为0，数据有问题"
        lines.append(f"{index}. {table_name} | cnt={_format_count(cnt)} | {reason}")
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


def run(
    dry_run=False,
    mentions=None,
    sr_password=None,
    sr_backup_password=None,
    bot_id=None,
    target_date=None,
    skip_refresh=True,
    profile=None,
    country_name=None,
    platform_type=None,
    check_table=None,
    table_names=None,
):
    target_date = parse_date(target_date) or default_target_date()
    check_config = build_check_config(
        profile=profile,
        country_name=country_name,
        platform_type=platform_type,
        check_table=check_table,
        table_names=table_names,
    )
    config = get_starrocks_config(
        sr_password=sr_password,
        sr_backup_password=sr_backup_password,
    )
    if not skip_refresh:
        refresh_check_table(target_date=target_date, config=config, check_config=check_config)
    rows = fetch_check_rows(target_date=target_date, config=config, check_config=check_config)
    problems = find_problem_tables(rows)
    if not problems:
        print(
            f"✅ {check_config.country_name}{check_config.platform_type}DWD表T-1产出校验无异常，"
            f"统计日期 {target_date.strftime('%Y-%m-%d')}，跳过TV告警发送"
        )
        return {"success": True, "status_code": None, "response": "no_problems"}

    message = format_alert_message(rows, target_date=target_date, problems=problems, check_config=check_config)
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
    parser.add_argument("--skip-refresh", action="store_true", default=True, help="跳过建表和刷新校验表，只读取校验表；默认开启")
    parser.add_argument("--refresh", dest="skip_refresh", action="store_false", help="执行建表并刷新校验表后再读取")
    parser.add_argument("--sr-password", default=None, help="StarRocks 主账号密码")
    parser.add_argument("--sr-backup-password", default=None, help="StarRocks 备份账号密码")
    parser.add_argument("--bot-id", default=None, help="指定发送使用的 TV 机器人 ID")
    parser.add_argument("--profile", default=None, help="国家配置档，例如 id/ine/ph/th/mx/pk")
    parser.add_argument("--country-name", default=None, help="国家名称，例如 印尼、菲律宾、泰国")
    parser.add_argument("--platform-type", default=None, help="平台类型，例如 投放")
    parser.add_argument("--check-table", default=None, help="校验结果表，默认 testdb.test_dwd_ad_table_cnt_check")
    parser.add_argument("--table-names", default=None, help="逗号分隔的 DWD 表名列表，默认使用投放 13 张表")
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
        profile=args.profile,
        country_name=args.country_name,
        platform_type=args.platform_type,
        check_table=args.check_table,
        table_names=args.table_names,
    )
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
