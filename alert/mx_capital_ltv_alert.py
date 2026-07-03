#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
查询墨西哥资方 LTV 最新数据并发送 TV 告警。

依赖 DolphinScheduler 任务:
    ads_capital_ltv（资方ltv监测）

默认查询:
    select a.stat_date, a.capital, a.ltv,
           a.normal_loan_amt,
           a.normal_loan_amt / b.exchange_usd_rate,
           a.account_balance,
           a.account_balance / b.exchange_usd_rate,
           b.exchange_usd_rate
    from dm_dd_new.ads_capital_ltv a
    inner join dim.dim_currency_rate b on a.stat_date = b.stat_date
    where a.stat_date >= '2026-05-01'
      and a.capital = 'new_share' / 'chuanjin'
      and b.currency_code = 'MXN'
    order by a.stat_date desc
    limit 1
"""

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
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
    "MX_CAPITAL_LTV_TV_BOT_ID",
    "5d0be3c3-0e06-4134-bbbe-690d7ff28d1e",
)
DEFAULT_MENTIONS = [
    item.strip()
    for item in os.environ.get(
        "MX_CAPITAL_LTV_TV_MENTIONS",
        "adamyu@kn.group,gretchenhe@kn.group",
    ).split(",")
    if item.strip()
]

MONITOR_TABLE = "dm_dd_new.ads_capital_ltv"
DEFAULT_START_DATE = "2026-05-01"
CAPITAL_ORDER = ("new_share", "chuanjin")
CAPITAL_CHOICES = ("all",) + CAPITAL_ORDER
CAPITAL_LABELS = {
    "new_share": "墨西哥新分享ltv",
    "chuanjin": "墨西哥串金ltv",
}
BALANCE_LABELS = {
    "new_share": "信托账户余额",
    "chuanjin": "通道余额",
}
NEW_SHARE_BALANCE_USD_THRESHOLD = Decimal("1000000")
NEW_SHARE_NORMAL_LOAN_USD_THRESHOLD = Decimal("43000000")
CHUANJIN_BALANCE_USD_THRESHOLD = Decimal("20000")
CHUANJIN_NORMAL_LOAN_USD_THRESHOLD = Decimal("5730000")


StarRocksAccount = sr_client.StarRocksAccount
StarRocksConfig = sr_client.StarRocksConfig


def get_starrocks_config(sr_password=None, sr_backup_password=None):
    return sr_client.build_config(
        sr_password=sr_password,
        sr_backup_password=sr_backup_password,
        default_host="127.0.0.1",
        default_port=9030,
        default_db="dm_dd_new",
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


def _selected_capitals(capital=None, capitals=None):
    if capitals is not None:
        return list(capitals)
    if capital in (None, "all"):
        return list(CAPITAL_ORDER)
    if capital not in CAPITAL_ORDER:
        raise ValueError(f"Unsupported capital: {capital}")
    return [capital]


def fetch_capital_ltv_rows(target_date=None, config=None, sr_password=None, sr_backup_password=None, capital=None, capitals=None):
    if config is None:
        config = get_starrocks_config(
            sr_password=sr_password,
            sr_backup_password=sr_backup_password,
        )

    sql = (
        "select a.stat_date, a.capital, a.ltv, "
        "a.normal_loan_amt as normal_loan_amt_peso, "
        "a.normal_loan_amt / b.exchange_usd_rate as normal_loan_amt_usd, "
        "a.account_balance as account_balance_peso, "
        "a.account_balance / b.exchange_usd_rate as account_balance_usd, "
        "b.exchange_usd_rate "
        f"from {MONITOR_TABLE} a "
        "inner join dim.dim_currency_rate b "
        "on a.stat_date = b.stat_date "
        "and b.currency_code = 'MXN' "
        "where a.stat_date >= %s "
        "and a.capital = %s "
        "order by a.stat_date desc "
        "limit 1"
    )
    conn = get_connection(config=config)
    try:
        cursor = conn.cursor()
        rows = []
        for capital_item in _selected_capitals(capital=capital, capitals=capitals):
            cursor.execute(sql, (DEFAULT_START_DATE, capital_item))
            row = cursor.fetchone()
            if row:
                rows.append(row)
        return rows
    finally:
        conn.close()


def _decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _format_number(value):
    number = _decimal(value)
    if number is None:
        return "未查询到"
    normalized = number.quantize(Decimal("0.01")) if number != number.to_integral() else number.quantize(Decimal("1"))
    return f"{normalized:,.2f}".rstrip("0").rstrip(".")


def _format_date(value):
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return str(value or "未查询到")


def _format_wan_number(value):
    number = _decimal(value)
    if number is None:
        return "未查询到"
    return f"{(number / Decimal('10000')).quantize(Decimal('0.01'))}"


def _format_money_wan(peso_value, usd_value, usd_unit="万美元"):
    if _decimal(peso_value) is None and _decimal(usd_value) is None:
        return "未查询到"
    return f"{_format_wan_number(peso_value)}万比索，即 {_format_wan_number(usd_value)}{usd_unit}"


def _ltv_tag(capital, ltv):
    ltv_value = _decimal(ltv)
    if ltv_value is None:
        return "ltv值缺失，需检查数据产出"
    if capital == "new_share":
        if ltv_value >= Decimal("0.75"):
            return "在阈值0.75以上，需紧急介入"
        if ltv_value < Decimal("0.65"):
            return "在阈值0.75以下，需关注通道余额或者资产，是否需要减持"
        return "在阈值0.75以下，在合格线"
    if capital == "chuanjin":
        if ltv_value < Decimal("1.43"):
            return "在阈值1.43以下，需紧急介入线"
        if ltv_value < Decimal("1.9"):
            return "在阈值1.43以上，在合格线"
        return "在阈值1.43以上，但需关注通道余额或者资产"
    return "未配置资方阈值，请检查告警配置"


def _amount_tags(capital, balance_usd, normal_loan_usd):
    balance_value = _decimal(balance_usd)
    normal_loan_value = _decimal(normal_loan_usd)
    tags = []
    if capital == "new_share":
        if balance_value is not None and balance_value > NEW_SHARE_BALANCE_USD_THRESHOLD:
            tags.append("通道余额大于100万美金")
        if normal_loan_value is not None and normal_loan_value > NEW_SHARE_NORMAL_LOAN_USD_THRESHOLD:
            tags.append("质押正常在贷大于4300万美金")
    if capital == "chuanjin":
        if balance_value is not None and balance_value > CHUANJIN_BALANCE_USD_THRESHOLD:
            tags.append("通道余额大于2万美金")
        if normal_loan_value is not None and normal_loan_value > CHUANJIN_NORMAL_LOAN_USD_THRESHOLD:
            tags.append("质押正常在贷大于573万美金")
    return tags


def _sort_rows(rows):
    order = {capital: index for index, capital in enumerate(CAPITAL_ORDER)}
    return sorted(rows, key=lambda row: order.get(str(row.get("capital")), 99))


def format_alert_message(rows, target_date=None, capital=None, capitals=None):
    target_date = parse_date(target_date) or default_target_date()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "🚨 墨西哥资方ltv告警",
        f"告警时间: {now}",
        f"依赖任务: ads_capital_ltv（资方ltv监测）",
    ]

    rows_by_capital = {str(row.get("capital") or ""): row for row in _sort_rows(rows)}

    for capital in _selected_capitals(capital=capital, capitals=capitals):
        row = rows_by_capital.get(capital)
        if row:
            ltv = row.get("ltv")
            tags = [_ltv_tag(capital, ltv)]
            tags.extend(
                _amount_tags(
                    capital,
                    row.get("account_balance_usd"),
                    row.get("normal_loan_amt_usd"),
                )
            )
        else:
            row = {
                "stat_date": target_date,
                "normal_loan_amt_peso": None,
                "normal_loan_amt_usd": None,
                "account_balance_peso": None,
                "account_balance_usd": None,
                "exchange_usd_rate": None,
                "ltv": None,
            }
            ltv = None
            tags = [f"未查询到该资方 LTV 数据，需检查 {MONITOR_TABLE} 产出"]

        lines.extend(
            [
                "",
                f"告警项: {CAPITAL_LABELS.get(capital, capital or '未知资方')}",
                f"统计日: {_format_date(row.get('stat_date')) or target_date.strftime('%Y-%m-%d')}",
                f"{BALANCE_LABELS.get(capital, '账户余额')}: {_format_money_wan(row.get('account_balance_peso', row.get('account_balance')), row.get('account_balance_usd'), usd_unit='万 美元')}",
                f"质押正常在贷: {_format_money_wan(row.get('normal_loan_amt_peso', row.get('normal_loan_amt')), row.get('normal_loan_amt_usd'))}",
                f"ltv值: {_format_number(ltv)}",
                f"兑美元汇率: {_format_number(row.get('exchange_usd_rate'))}",
                f"附加标签: {'；'.join(tags)}",
            ]
        )
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


def run(dry_run=False, mentions=None, sr_password=None, sr_backup_password=None, bot_id=None, target_date=None, capital=None):
    target_date = parse_date(target_date) or default_target_date()
    config = get_starrocks_config(
        sr_password=sr_password,
        sr_backup_password=sr_backup_password,
    )
    rows = fetch_capital_ltv_rows(target_date=target_date, config=config, capital=capital)
    message = format_alert_message(rows, target_date=target_date, capital=capital)
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
    parser = argparse.ArgumentParser(description="查询墨西哥资方 LTV T-1 数据并发送 TV 告警")
    parser.add_argument("--dry-run", action="store_true", help="只打印消息，不发送 TV")
    parser.add_argument("--target-date", default=None, help="指定统计日，格式 YYYY-MM-DD；默认 T-1")
    parser.add_argument("--sr-password", default=None, help="StarRocks 主账号密码")
    parser.add_argument("--sr-backup-password", default=None, help="StarRocks 备份账号密码")
    parser.add_argument("--bot-id", default=None, help="指定发送使用的 TV 机器人 ID")
    parser.add_argument(
        "--capital",
        choices=CAPITAL_CHOICES,
        default="all",
        help="指定告警资方：all/new_share/chuanjin；默认 all",
    )
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
        capital=args.capital,
    )
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
