#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统计 StarRocks 数仓与财务库/biz库数据一致性校验表记录数并发送 TV 告警。

一次运行同时发送两条告警（同一脚本内，与既有告警放在一起）：
1. 数仓与财务库数据一致性校验
   查询:
     select count(1) from fin.fin_manage_ods_data_quality_monitor where dt = (select max(dt) from fin.fin_manage_ods_data_quality_monitor)
     select count(1) from fin.fin_manage_ods_data_quality_monitor where dt = (select max(dt) from fin.fin_manage_ods_data_quality_monitor) and diff <> 0
2. 数仓与biz库数据一致性校验（全球 PL 对账，查询表沿用 fin.fin_manage_ods_data_quality_monitor）
   校验内容:
     - cw_catalog.capital.bi_* vs ods_security.ods_capital_bi_*
     - 五国 ODS 非经营费用 vs fin_global.pl_nonoperate_expense_monthly_global（expense_local / expense_usd，历史月）
     - 五国 ODS 非经营费用 vs fin_global.manage_model_pl_operational_cost_apportion_global（self_owned_fund_income，历史月）
   异常规则: 只统计 abs(diff) > 1 的记录（绝对值过滤科学计数法浮点噪声，如 5e-7）；
   统计范围忽略 table_name 以 fin_global. 开头的记录（仅保留 cw_catalog.capital.bi_* 三表对账）

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

MONITOR_TABLE = "fin.fin_manage_ods_data_quality_monitor"
LATEST_BATCH_TOTAL_COUNT_SQL = (
    f"select count(1) as alert_count from {MONITOR_TABLE} "
    f"where dt = (select max(dt) from {MONITOR_TABLE})"
)
LATEST_BATCH_EXCEPTION_COUNT_SQL = (
    f"select count(1) as alert_count from {MONITOR_TABLE} "
    f"where dt = (select max(dt) from {MONITOR_TABLE}) and diff <> 0"
)

# ---------- biz库告警（全球 PL 对账） ----------
# 阈值：异常只统计 abs(diff) > 1 的记录（绝对值避免科学计数法浮点噪声，如 5e-7）
DIFF_THRESHOLD = 1

# 与用户提供的原始查询保持一致，仅做两处等价改写：
#   1) 去掉首行 `set @v_start_date = current_date;`，把 @v_start_date 直接写成 current_date()；
#   2) 拆分 CTE 与 UNION 主体，分别包成总记录数 / 异常记录数两个 count 查询。
BIZ_CTE_CLAUSE = r"""
with nonoperate_ods_base as (
    select stat_month, 'PK' as country,
           sum(coalesce(expense_local, 0)) as expense_local,
           sum(coalesce(expense_usd, 0)) as expense_usd
    from fin_global.ods_pk_pl_nonoperate_expense_monthly
    group by stat_month
    union all
    select stat_month, 'MX', sum(coalesce(expense_local, 0)), sum(coalesce(expense_usd, 0))
    from fin_global.ods_mx_pl_nonoperate_expense_monthly
    group by stat_month
    union all
    select stat_month, 'PH', sum(coalesce(expense_local, 0)), sum(coalesce(expense_usd, 0))
    from fin_global.ods_ph_pl_nonoperate_expense_monthly
    group by stat_month
    union all
    select stat_month, 'TH', sum(coalesce(expense_local, 0)), sum(coalesce(expense_usd, 0))
    from fin_global.ods_th_pl_nonoperate_expense_monthly
    group by stat_month
    union all
    select stat_month, 'INE', sum(coalesce(expense_local, 0)), sum(coalesce(expense_usd, 0))
    from fin_global.ods_ine_pl_nonoperate_expense_monthly
    group by stat_month
),
nonoperate_global_base as (
    select stat_month, country,
           sum(coalesce(expense_local, 0)) as expense_local,
           sum(coalesce(expense_usd, 0)) as expense_usd
    from fin_global.pl_nonoperate_expense_monthly_global
    group by stat_month, country
),
nonoperate_result_base as (
    select
        stat_month,
        case country
            when 'g_巴基斯坦' then 'PK'
            when 'e_墨西哥' then 'MX'
            when 'f_菲律宾' then 'PH'
            when 'd_泰国' then 'TH'
            when 'c_印尼' then 'INE'
        end as country,
        currency_type,
        fin_apportion_typer,
        sum(coalesce(stat_amounts, 0)) as stat_amounts
    from fin_global.manage_model_pl_operational_cost_apportion_global
    where stat_subject = 'self_owned_fund_income'
      and country in ('g_巴基斯坦', 'e_墨西哥', 'f_菲律宾', 'd_泰国', 'c_印尼')
      and currency_type in ('业务国本币', '美元')
    group by stat_month, country, currency_type, fin_apportion_typer
)
"""

BIZ_UNION_SELECT = r"""
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

    union all
    select
        current_date() as dt
        ,concat('fin_global.ods_', lower(coalesce(a.country, b.country)), '_pl_nonoperate_expense_monthly.expense_local') as table_name
        ,cast(replace(coalesce(a.stat_month, b.stat_month), '-', '') as bigint) as mysql_primary_key
        ,case when coalesce(a.stat_month, b.stat_month) < date_format(current_date(), '%Y-%m')
              then coalesce(a.expense_local, 0) else 0 end as src_value
        ,coalesce(b.expense_local, 0) as dest_value
        ,case when coalesce(a.stat_month, b.stat_month) < date_format(current_date(), '%Y-%m')
              then coalesce(a.expense_local, 0) else 0 end - coalesce(b.expense_local, 0) as diff
    from nonoperate_ods_base a
    full outer join nonoperate_global_base b
      on b.country = a.country
     and b.stat_month = a.stat_month

    union all
    select
        current_date() as dt
        ,concat('fin_global.ods_', lower(coalesce(a.country, b.country)), '_pl_nonoperate_expense_monthly.expense_usd') as table_name
        ,cast(replace(coalesce(a.stat_month, b.stat_month), '-', '') as bigint) as mysql_primary_key
        ,case when coalesce(a.stat_month, b.stat_month) < date_format(current_date(), '%Y-%m')
              then coalesce(a.expense_usd, 0) else 0 end as src_value
        ,coalesce(b.expense_usd, 0) as dest_value
        ,case when coalesce(a.stat_month, b.stat_month) < date_format(current_date(), '%Y-%m')
              then coalesce(a.expense_usd, 0) else 0 end - coalesce(b.expense_usd, 0) as diff
    from nonoperate_ods_base a
    full outer join nonoperate_global_base b
      on b.country = a.country
     and b.stat_month = a.stat_month

    union all
    select
        current_date() as dt
        ,concat('fin_global.manage_model_pl_operational_cost_apportion_global.',
                lower(coalesce(a.country, b.country)), '.self_owned_fund_income.',
                case when coalesce(b.currency_type, e.currency_type) = '业务国本币' then 'local' else 'usd' end, '.',
                case when coalesce(b.fin_apportion_typer, e.fin_apportion_typer) = '分摊前' then 'before' else 'after' end) as table_name
        ,cast(replace(coalesce(a.stat_month, b.stat_month), '-', '') as bigint) as mysql_primary_key
        ,case
            when coalesce(a.stat_month, b.stat_month) >= date_format(current_date(), '%Y-%m') then 0
            when coalesce(b.currency_type, e.currency_type) = '美元' then -coalesce(a.expense_usd, 0)
            else -coalesce(a.expense_local, 0)
         end as src_value
        ,coalesce(b.stat_amounts, 0) as dest_value
        ,case
            when coalesce(a.stat_month, b.stat_month) >= date_format(current_date(), '%Y-%m') then 0
            when coalesce(b.currency_type, e.currency_type) = '美元' then -coalesce(a.expense_usd, 0)
            else -coalesce(a.expense_local, 0)
         end - coalesce(b.stat_amounts, 0) as diff
    from nonoperate_ods_base a
    cross join (
        select '业务国本币' as currency_type, '分摊前' as fin_apportion_typer
        union all select '业务国本币', '分摊后'
        union all select '美元', '分摊前'
        union all select '美元', '分摊后'
    ) e
    full outer join nonoperate_result_base b
      on b.country = a.country
     and b.stat_month = a.stat_month
     and b.currency_type = e.currency_type
     and b.fin_apportion_typer = e.fin_apportion_typer
"""

# biz库告警查询表沿用财务库监控表（与用户确认：查询表固定为 fin.fin_manage_ods_data_quality_monitor）
BIZ_MONITOR_TABLE = MONITOR_TABLE
# 总记录数：全部对账行（忽略 table_name 以 fin_global. 开头的记录）
BIZ_LATEST_BATCH_TOTAL_COUNT_SQL = (
    BIZ_CTE_CLAUSE + f"select count(1) as alert_count from ({BIZ_UNION_SELECT}) __t "
    f"where __t.table_name not like 'fin_global.%'"
)
# 异常记录数：只统计 abs(diff) > 1 的记录（绝对值过滤 5e-7 这类科学计数法浮点噪声），
# 并忽略 table_name 以 fin_global. 开头的记录
BIZ_LATEST_BATCH_EXCEPTION_COUNT_SQL = (
    BIZ_CTE_CLAUSE + f"select count(1) as alert_count from ({BIZ_UNION_SELECT}) __t "
    f"where __t.table_name not like 'fin_global.%' and abs(__t.diff) > {DIFF_THRESHOLD}"
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


def fetch_biz_latest_batch_counts(config=None, sr_password=None, sr_backup_password=None):
    """biz库告警：全球 PL 对账记录数 + abs(diff)>1 异常记录数（忽略 fin_global. 开头的表）。"""
    if config is None:
        config = get_starrocks_config(
            sr_password=sr_password,
            sr_backup_password=sr_backup_password,
        )
    return _fetch_counts_from_sql(
        BIZ_LATEST_BATCH_TOTAL_COUNT_SQL,
        BIZ_LATEST_BATCH_EXCEPTION_COUNT_SQL,
        config=config,
    )


def format_alert_message(alert_count, exception_count, title, monitor_table=MONITOR_TABLE, mention_labels=None):
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
        monitor_table=MONITOR_TABLE,
    )


def format_biz_alert_message(alert_count, exception_count, mention_labels=None):
    return format_alert_message(
        alert_count,
        exception_count,
        title="数仓与biz库数据一致性校验",
        monitor_table=BIZ_MONITOR_TABLE,
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
    fin_counts = fetch_latest_batch_counts(config=config)
    biz_counts = fetch_biz_latest_batch_counts(config=config)

    fin_message = format_fin_alert_message(
        alert_count=fin_counts["alert_count"],
        exception_count=fin_counts["exception_count"],
    )
    biz_message = format_biz_alert_message(
        alert_count=biz_counts["alert_count"],
        exception_count=biz_counts["exception_count"],
    )
    if not fin_message.endswith("\n"):
        fin_message = f"{fin_message}\n"
    if not biz_message.endswith("\n"):
        biz_message = f"{biz_message}\n"

    if dry_run:
        print(fin_message)
        print()
        print(biz_message)
        return {"success": True, "status_code": None, "response": "dry_run"}

    # 一次运行同时发送两条告警
    results = [
        send_to_tv(fin_message, mentions=mentions, bot_id=bot_id),
        send_to_tv(biz_message, mentions=mentions, bot_id=bot_id),
    ]
    success = all(result["success"] for result in results)
    if success:
        print(f"✅ TV告警发送成功（财务库 + biz库，共 {len(results)} 条）")
    else:
        for index, result in enumerate(results, start=1):
            if result["success"]:
                print(f"✅ 第 {index} 条 TV告警发送成功 (HTTP {result['status_code']})")
            else:
                print(f"❌ 第 {index} 条 TV告警发送失败 (HTTP {result['status_code']})")
                print(result["response"])
    return {"success": success, "status_code": None, "response": "ok"}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="统计 StarRocks 数仓与财务库/biz库数据一致性校验记录数并发送 TV 告警")
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
