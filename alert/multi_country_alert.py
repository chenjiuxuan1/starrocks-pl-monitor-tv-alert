#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多国一致性校验告警（模板：multi_country）。

由平台「告警注册」合成：sqlBlocks 的 CHECK_SQL 键会注入到下方占位符。
每个国家一条平台条目，共用本模板；通过 --country 与 SR_* 环境变量区分各国连接。

通知通道（可同时启用）：
  - TV 告警（默认，send_to_tv）
  - KN Chat 私信/群发（提供 --knchat-chat-id，token 读环境变量 KNCHAT_BOT_TOKEN）

真实密码请通过环境变量传入:
    SR_PASSWORD=... SR_BACKUP_PASSWORD=... python3 alert/multi_country_alert.py --country cn --knchat-chat-id -10XXXXXXX
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alert.common import knchat_sender, sr_client, tv_sender

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
    "MULTI_COUNTRY_TV_BOT_ID",
    "f82292a5-45c5-42ea-84da-272b4c81ebcc",
)
DEFAULT_MENTIONS = [
    item.strip()
    for item in os.environ.get(
        "MULTI_COUNTRY_TV_MENTIONS",
        "adamyu@kn.group",
    ).split(",")
    if item.strip()
]

# ---------- 校验 SQL（由平台 SQL 块注入；各国条目各自填本国 CHECK_SQL） ----------
CHECK_SQL = r"""
-- TODO: 各国校验 SQL（占位）
select 1 as alert_count from dual where 1=0
"""

# 国家显示名映射（--country 值 → 中文名），用于告警文案
COUNTRY_NAMES = {
    "cn": "中国",
    "id": "印尼",
    "ine": "印尼",
    "indonesia": "印尼",
    "mx": "墨西哥",
    "mexico": "墨西哥",
    "th": "泰国",
    "thailand": "泰国",
    "ph": "菲律宾",
    "philippines": "菲律宾",
    "pk": "巴基斯坦",
    "pakistan": "巴基斯坦",
}


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


def get_connection(config=None):
    return sr_client.get_connection(config or get_starrocks_config())


def query_one(sql, params=None, config=None):
    return sr_client.query_one(sql, params=params, config=config)


def query_all(sql, params=None, config=None):
    return sr_client.query_all(sql, params=params, config=config)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="多国一致性校验告警")
    parser.add_argument("--country", default="cn", help="国家标识：cn/id/ine/mx/th/ph/pk 等")
    parser.add_argument("--dry-run", action="store_true", help="只打印消息，不发送任何通知")
    parser.add_argument("--sr-password", default=None, help="StarRocks 主账号密码")
    parser.add_argument("--sr-backup-password", default=None, help="StarRocks 备份账号密码")
    parser.add_argument("--bot-id", default=None, help="TV 机器人 ID")
    parser.add_argument("--mentions", default="", help="逗号分隔的提醒邮箱列表")
    parser.add_argument("--knchat-chat-id", default=None, help="KN Chat 目标会话 ID（群/私聊），逗号分隔可多个")
    parser.add_argument("--knchat-token", default=None, help="KN Chat 机器人 token；缺省读环境变量 KNCHAT_BOT_TOKEN")
    return parser.parse_args(argv)


def _build_message(country_name, rows, extra=""):
    lines = [
        f"🔔 多国一致性校验告警（{country_name}）",
        f"时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
    ]
    if rows is None:
        lines.append("校验执行失败：无法获取校验结果")
    else:
        lines.append(f"异常记录数：{rows}")
    if extra:
        lines.append(extra)
    return "\n".join(lines)


def run(
    dry_run=False,
    country="cn",
    mentions=None,
    sr_password=None,
    sr_backup_password=None,
    bot_id=None,
    knchat_chat_id=None,
    knchat_token=None,
):
    """执行校验并发送通知。返回 {success, message, rows, tv_result, knchat_result}。"""
    mentions = mentions if mentions is not None else DEFAULT_MENTIONS
    country_name = COUNTRY_NAMES.get(country, country)
    config = get_starrocks_config(sr_password=sr_password, sr_backup_password=sr_backup_password)

    # 1. 执行校验 SQL
    rows = None
    error = None
    try:
        row = query_one(f"select count(1) as alert_count from ({CHECK_SQL}) __t", config=config)
        rows = int(row["alert_count"]) if row else 0
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
        rows = None

    message = _build_message(country_name, rows, extra=f"异常信息：{error}" if error else "")
    print(message)

    tv_result = None
    knchat_result = None
    if dry_run:
        print("（dry-run：不发送任何通知）")
        return {"success": True, "message": message, "rows": rows, "error": error, "tv_result": None, "knchat_result": None}

    # 2. TV 告警
    try:
        tv_result = tv_sender.send_to_tv(
            message,
            mentions=mentions,
            bot_id=bot_id or TV_BOT_ID,
            api_url=TV_API_URL,
        )
        print("TV 发送结果:", tv_result.get("status_code"), tv_result.get("response", "")[:120])
    except Exception as exc:  # noqa: BLE001
        tv_result = {"success": False, "response": str(exc)}
        print("TV 发送异常:", exc)

    # 3. KN Chat 私信/群发
    if knchat_chat_id:
        try:
            chat_ids = [c.strip() for c in str(knchat_chat_id).split(",") if c.strip()]
            knchat_result = []
            for chat_id in chat_ids:
                r = knchat_sender.send_message(chat_id, message, token=knchat_token)
                knchat_result.append({"chat_id": chat_id, **r})
                if not r.get("success"):
                    print(f"❌ KN Chat 私信失败 chat_id={chat_id}: {r.get('description') or r.get('error_code')}")
                else:
                    print(f"✅ KN Chat 已发送 chat_id={chat_id} (message_id={r.get('result', {}).get('message_id')})")
        except Exception as exc:  # noqa: BLE001
            knchat_result = [{"error": str(exc)}]
            print("KN Chat 发送异常:", exc)

    success = error is None and (tv_result is None or tv_result.get("success", False)) and (knchat_result is None or all(r.get("success", False) for r in knchat_result if "success" in r))
    return {
        "success": success,
        "message": message,
        "rows": rows,
        "error": error,
        "tv_result": tv_result,
        "knchat_result": knchat_result,
    }


if __name__ == "__main__":
    args = parse_args()
    mentions = [item.strip() for item in args.mentions.split(",") if item.strip()]
    result = run(
        dry_run=args.dry_run,
        country=args.country,
        mentions=mentions,
        sr_password=args.sr_password,
        sr_backup_password=args.sr_backup_password,
        bot_id=args.bot_id,
        knchat_chat_id=args.knchat_chat_id,
        knchat_token=args.knchat_token,
    )
    sys.exit(0 if result["success"] else 1)
