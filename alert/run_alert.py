#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unified entrypoint for TV alert scripts.

支持通过 --knchat-chat-id 额外把告警结果私信/群发到 KN Chat（可选）。
"""

import argparse
import importlib
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alert.common import knchat_sender  # noqa: E402


ALERT_MODULES = {
    "pl_monitor": "alert.manage_model_global_pl_monitor_alert",
    "fin_ods_quality": "alert.fin_manage_ods_data_quality_monitor_alert",
    "mx_capital_ltv": "alert.mx_capital_ltv_alert",
    "id_marketing_dwd_cnt": "alert.id_marketing_dwd_table_cnt_alert",
    "marketing_dwd_cnt": "alert.id_marketing_dwd_table_cnt_alert",
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
    parser.add_argument("--skip-refresh", action="store_true", default=True, help="投放 DWD 校验只读取校验表；默认开启")
    parser.add_argument("--profile", default=None, help="投放 DWD 国家配置档，例如 id/ine/ph/th/mx/pk")
    parser.add_argument("--country-name", default=None, help="投放 DWD 校验国家名称，例如 印尼、菲律宾、泰国")
    parser.add_argument("--platform-type", default=None, help="投放 DWD 平台类型，例如 投放")
    parser.add_argument("--check-table", default=None, help="投放 DWD 校验结果表")
    parser.add_argument("--table-names", default=None, help="逗号分隔的投放 DWD 表名列表")
    parser.add_argument("--limit", type=int, default=None, help="兼容旧告警脚本参数")
    parser.add_argument("--section", default=None, choices=["fin", "biz", "all"], help="PL 财务/biz 对账分段：fin（仅财务库）/ biz（仅 biz 库）/ all（默认）")
    parser.add_argument("--knchat-chat-id", default=None, help="KN Chat 目标会话 ID（群/私聊），逗号分隔可多个；提供则额外私信/群发告警结果")
    parser.add_argument("--knchat-token", default=None, help="KN Chat 机器人 token；缺省读环境变量 KNCHAT_BOT_TOKEN")
    return parser.parse_args(argv)


def send_knchat(chat_ids, text, token):
    """把 text 私信/群发到各 chat_id，返回 (ok, 错误列表)。"""
    if not chat_ids or not text:
        return True, []
    errors = []
    ok = True
    for chat_id in chat_ids:
        result = knchat_sender.send_message(chat_id, text, token=token)
        if not result.get("success"):
            ok = False
            errors.append(f"chat_id={chat_id}: {result.get('description') or result.get('error_code')}")
    return ok, errors


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
    if args.alert in ("id_marketing_dwd_cnt", "marketing_dwd_cnt"):
        run_kwargs["skip_refresh"] = True
        run_kwargs["profile"] = args.profile
        run_kwargs["country_name"] = args.country_name
        run_kwargs["platform_type"] = args.platform_type
        run_kwargs["check_table"] = args.check_table
        run_kwargs["table_names"] = args.table_names
    if args.limit is not None and args.alert in ("pl_monitor", "fin_ods_quality"):
        run_kwargs["limit"] = args.limit
    if args.section and args.alert == "fin_ods_quality":
        run_kwargs["section"] = args.section

    result = alert_module.run(**run_kwargs)
    tv_ok = bool(result.get("success"))

    # KN Chat 私信/群发：非 dry-run 且配置了 chat_id 时执行
    knchat_ok = True
    chat_ids = [c.strip() for c in (args.knchat_chat_id or "").split(",") if c.strip()]
    if chat_ids and not args.dry_run:
        message = result.get("message") or result.get("response")
        if not message:
            message = f"{alert_module.__name__} 告警执行完成（exit ok={tv_ok}）"
        knchat_ok, errors = send_knchat(chat_ids, message, args.knchat_token)
        if not knchat_ok:
            for err in errors:
                print(f"❌ KN Chat 私信失败: {err}")

    return 0 if (tv_ok and knchat_ok) else 1


if __name__ == "__main__":
    sys.exit(main())

