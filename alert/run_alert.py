#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unified entrypoint for TV alert scripts."""

import argparse
import importlib
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


ALERT_MODULES = {
    "pl_monitor": "alert.manage_model_global_pl_monitor_alert",
    "fin_ods_quality": "alert.fin_manage_ods_data_quality_monitor_alert",
    "mx_capital_ltv": "alert.mx_capital_ltv_alert",
    "id_marketing_dwd_cnt": "alert.id_marketing_dwd_table_cnt_alert",
}


def parse_date(value):
    if value is None:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="统一 TV 告警入口")
    parser.add_argument("--alert", required=True, choices=sorted(ALERT_MODULES), help="告警类型")
    parser.add_argument("--dry-run", action="store_true", help="只打印消息，不发送 TV")
    parser.add_argument("--target-date", default=None, help="指定统计日，格式 YYYY-MM-DD")
    parser.add_argument("--sr-password", default=None, help="StarRocks 主账号密码")
    parser.add_argument("--sr-backup-password", default=None, help="StarRocks 备份账号密码")
    parser.add_argument("--bot-id", default=None, help="指定发送使用的 TV 机器人 ID")
    parser.add_argument("--mentions", default="", help="逗号分隔的提醒邮箱列表")
    parser.add_argument("--capital", default=None, help="墨西哥资方 LTV 参数：all/new_share/chuanjin")
    parser.add_argument("--skip-refresh", action="store_true", help="印尼投放 DWD 校验跳过刷新")
    parser.add_argument("--limit", type=int, default=None, help="兼容旧告警脚本参数")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    alert_module = ALERT_MODULES[args.alert]
    if isinstance(alert_module, str):
        alert_module = importlib.import_module(alert_module)
    mentions = [item.strip() for item in args.mentions.split(",") if item.strip()]
    run_kwargs = {
        "dry_run": args.dry_run,
        "mentions": mentions,
        "sr_password": args.sr_password,
        "sr_backup_password": args.sr_backup_password,
        "bot_id": args.bot_id,
    }

    if args.target_date:
        run_kwargs["target_date"] = parse_date(args.target_date)
    if args.alert == "mx_capital_ltv" and args.capital:
        run_kwargs["capital"] = args.capital
    if args.alert == "id_marketing_dwd_cnt":
        run_kwargs["skip_refresh"] = args.skip_refresh
    if args.limit is not None and args.alert in ("pl_monitor", "fin_ods_quality"):
        run_kwargs["limit"] = args.limit

    result = alert_module.run(**run_kwargs)
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
