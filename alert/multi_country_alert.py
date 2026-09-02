#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多国一致性校验告警（模板：multi_country）。

由平台「告警注册」合成：sqlBlocks 的 CHECK_SQL 键会注入到下方占位符。
每个国家一条平台条目，共用本模板；通过 --country 与 SR_* 环境变量区分各国连接。

通知通道（可同时启用）：
  - TV 告警（默认，send_to_tv）
  - KN Chat 私信/群发（提供 --knchat-chat-id，token 读环境变量 KNCHAT_BOT_TOKEN）

【测试群约定】所有测试消息发送到「PL告警测试群」chat_id=-10950（数仓告警机器人
Data_Warehouse_Alarm_Robot 已在群内）。正式告警目标群按各自需求另行指定。

真实密码请通过环境变量传入:
    SR_PASSWORD=... SR_BACKUP_PASSWORD=... python3 alert/multi_country_alert.py --country cn --knchat-chat-id -10950
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
with asset_grant_scope as (
    select
        asset_item_no,
        user_flag,
        user_debt_status,
        grant_time,
        due_time,
        delay_due_time,
        finish_time,
        contract_principal_amt,
        granted_principal_amt,
        interest_amt,
        fee_amt
    from dwb.dwb_asset_info
    where grant_time >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 day)
      and grant_time < DATE_SUB(CURRENT_DATE(), INTERVAL 2 day)
),
period_grant_scope as (
    select
        asset_item_no,
        period_seq,
        user_flag,
        user_debt_status,
        grant_time,
        due_time,
        delay_due_time,
        finish_time,
        contract_principal_period_amt,
        granted_principal_period_amt,
        interest_period_amt,
        fee_period_amt
    from dwb.dwb_asset_period_info
    where grant_time >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 day)
      and grant_time < DATE_SUB(CURRENT_DATE(), INTERVAL 2 day)
),
asset_due_scope as (
    select
        asset_item_no,
        due_time,
        repaid_principal_amt,
        repaid_interest_amt,
        repaid_fee_amt,
        extra_amt,
        repaid_extra_amt,
        reduce_amt,
        penalty_amt,
        repaid_penalty_amt
    from dwb.dwb_asset_info
    where due_time >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 day)
      and due_time < DATE_SUB(CURRENT_DATE(), INTERVAL 2 day)
),
period_due_scope as (
    select
        p.asset_item_no,
        p.repaid_principal_period_amt,
        p.repaid_interest_period_amt,
        p.repaid_fee_period_amt,
        p.extra_period_amt,
        p.repaid_extra_period_amt,
        p.reduce_period_amt,
        p.penalty_period_amt,
        p.repaid_penalty_period_amt
    from dwb.dwb_asset_period_info p
    join asset_due_scope a
      on p.asset_item_no = a.asset_item_no
),
last_period as (
    select asset_item_no, due_time, delay_due_time, finish_time
    from (
        select
            asset_item_no,
            period_seq,
            due_time,
            delay_due_time,
            finish_time,
            row_number() over (
                partition by asset_item_no
                order by period_seq desc
            ) as rn
        from period_grant_scope
    ) t
    where rn = 1
),
grant_amt_rollup as (
    select
        asset_item_no,
        sum(contract_principal_period_amt) as contract_principal_amt,
        sum(granted_principal_period_amt) as granted_principal_amt,
        sum(interest_period_amt) as interest_amt,
        sum(fee_period_amt) as fee_amt
    from period_grant_scope
    group by asset_item_no
),
due_amt_rollup as (
    select
        asset_item_no,
        sum(repaid_principal_period_amt) as repaid_principal_amt,
        sum(repaid_interest_period_amt) as repaid_interest_amt,
        sum(repaid_fee_period_amt) as repaid_fee_amt,
        sum(extra_period_amt) as extra_amt,
        sum(repaid_extra_period_amt) as repaid_extra_amt,
        sum(reduce_period_amt) as reduce_amt,
        sum(penalty_period_amt) as penalty_amt,
        sum(repaid_penalty_period_amt) as repaid_penalty_amt
    from period_due_scope
    group by asset_item_no
),
first_asset as (
    select
        user_id,
        asset_item_no,
        grant_time,
        granted_principal_amt
    from (
        select
            user_id,
            asset_item_no,
            grant_time,
            granted_principal_amt,
            row_number() over (
                partition by user_id
                order by grant_time, asset_item_no
            ) as rn
        from dwb.dwb_asset_info
        where asset_status in ('payoff', 'repay')
          and asset_loan_channel <> 'noloan'
          and coalesce(asset_source_flag, '') <> 'PAK007导流PAK009'
    ) t
    where rn = 1
      and grant_time >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 day)
      and grant_time < DATE_SUB(CURRENT_DATE(), INTERVAL 2 day)
),
cross_check as (
    select 'user_flag' as check_item, count(distinct a.asset_item_no) as mismatch_cnt
    from asset_grant_scope a
    left join period_grant_scope p on a.asset_item_no = p.asset_item_no
    where p.asset_item_no is null or not (a.user_flag <=> p.user_flag)
    union all
    select 'user_debt_status', count(distinct a.asset_item_no)
    from asset_grant_scope a
    left join period_grant_scope p on a.asset_item_no = p.asset_item_no
    where p.asset_item_no is null or not (a.user_debt_status <=> p.user_debt_status)
    union all
    select 'grant_time', count(distinct a.asset_item_no)
    from asset_grant_scope a
    left join period_grant_scope p on a.asset_item_no = p.asset_item_no
    where p.asset_item_no is null or not (a.grant_time <=> p.grant_time)
    union all
    select 'due_time', count(*)
    from asset_grant_scope a
    left join last_period p on a.asset_item_no = p.asset_item_no
    where p.asset_item_no is null or not (a.due_time <=> p.due_time)
    union all
    select 'delay_due_time', count(*)
    from asset_grant_scope a
    left join last_period p on a.asset_item_no = p.asset_item_no
    where p.asset_item_no is null or not (a.delay_due_time <=> p.delay_due_time)
    union all
    select 'finish_time', count(*)
    from asset_grant_scope a
    left join last_period p on a.asset_item_no = p.asset_item_no
    where p.asset_item_no is null or not (a.finish_time <=> p.finish_time)
    union all
    select 'contract_principal_amt', count(*)
    from asset_grant_scope a
    left join grant_amt_rollup p on a.asset_item_no = p.asset_item_no
    where p.asset_item_no is null or not (a.contract_principal_amt <=> p.contract_principal_amt)
    union all
    select 'granted_principal_amt', count(*)
    from asset_grant_scope a
    left join grant_amt_rollup p on a.asset_item_no = p.asset_item_no
    where p.asset_item_no is null or not (a.granted_principal_amt <=> p.granted_principal_amt)
    union all
    select 'interest_amt', count(*)
    from asset_grant_scope a
    left join grant_amt_rollup p on a.asset_item_no = p.asset_item_no
    where p.asset_item_no is null or not (a.interest_amt <=> p.interest_amt)
    union all
    select 'fee_amt', count(*)
    from asset_grant_scope a
    left join grant_amt_rollup p on a.asset_item_no = p.asset_item_no
    where p.asset_item_no is null or not (a.fee_amt <=> p.fee_amt)
    union all
    select 'repaid_principal_amt', count(*)
    from asset_due_scope a
    left join due_amt_rollup p on a.asset_item_no = p.asset_item_no
    where p.asset_item_no is null or not (a.repaid_principal_amt <=> p.repaid_principal_amt)
    union all
    select 'repaid_interest_amt', count(*)
    from asset_due_scope a
    left join due_amt_rollup p on a.asset_item_no = p.asset_item_no
    where p.asset_item_no is null or not (a.repaid_interest_amt <=> p.repaid_interest_amt)
    union all
    select 'repaid_fee_amt', count(*)
    from asset_due_scope a
    left join due_amt_rollup p on a.asset_item_no = p.asset_item_no
    where p.asset_item_no is null or not (a.repaid_fee_amt <=> p.repaid_fee_amt)
    union all
    select 'extra_amt', count(*)
    from asset_due_scope a
    left join due_amt_rollup p on a.asset_item_no = p.asset_item_no
    where p.asset_item_no is null or not (a.extra_amt <=> p.extra_amt)
    union all
    select 'repaid_extra_amt', count(*)
    from asset_due_scope a
    left join due_amt_rollup p on a.asset_item_no = p.asset_item_no
    where p.asset_item_no is null or not (a.repaid_extra_amt <=> p.repaid_extra_amt)
    union all
    select 'reduce_amt', count(*)
    from asset_due_scope a
    left join due_amt_rollup p on a.asset_item_no = p.asset_item_no
    where p.asset_item_no is null or not (a.reduce_amt <=> p.reduce_amt)
    union all
    select 'penalty_amt', count(*)
    from asset_due_scope a
    left join due_amt_rollup p on a.asset_item_no = p.asset_item_no
    where p.asset_item_no is null or not (a.penalty_amt <=> p.penalty_amt)
    union all
    select 'repaid_penalty_amt', count(*)
    from asset_due_scope a
    left join due_amt_rollup p on a.asset_item_no = p.asset_item_no
    where p.asset_item_no is null or not (a.repaid_penalty_amt <=> p.repaid_penalty_amt)
    union all
    select 'first_asset_item_no', count(*)
    from first_asset a
    left join dwb.dwb_user_info u on a.user_id = u.user_id
    where u.user_id is null or not (u.first_asset_item_no <=> a.asset_item_no)
    union all
    select 'first_grant_time', count(*)
    from first_asset a
    left join dwb.dwb_user_info u on a.user_id = u.user_id
    where u.user_id is null or not (u.first_grant_time <=> a.grant_time)
    union all
    select 'first_grant_amt', count(*)
    from first_asset a
    left join dwb.dwb_user_info u on a.user_id = u.user_id
    where u.user_id is null or not (u.first_grant_amt <=> a.granted_principal_amt)
)
select
    check_item,
    mismatch_cnt
from cross_check
order by check_item;
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


def _build_message(country_name, mismatch_items, error=None):
    """按校验结果构造告警文案。mismatch_items = [(check_item, mismatch_cnt), ...]（仅异常项）。"""
    lines = [
        f"🔔 多国一致性校验告警（{country_name}）",
        f"时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
    ]
    if error:
        lines.append("校验执行失败：无法获取校验结果")
        lines.append(f"异常信息：{error}")
    elif mismatch_items:
        lines.append(f"发现 {len(mismatch_items)} 项不一致：")
        for item, cnt in mismatch_items:
            lines.append(f"  - {item}: {cnt}")
    else:
        lines.append("全部校验项通过（mismatch_cnt 均为 0）")
    return "\n".join(lines)


def _run_check(config):
    """执行 CHECK_SQL，返回 (mismatch_items, error)。
    mismatch_items = [(check_item, mismatch_cnt), ...]（仅 mismatch_cnt > 0 的项）。
    """
    try:
        rows = query_all(f"select check_item, mismatch_cnt from ({CHECK_SQL}) __t", config=config)
        mismatch_items = []
        for row in rows:
            item = row.get("check_item")
            try:
                cnt = int(row.get("mismatch_cnt") or 0)
            except (TypeError, ValueError):
                cnt = 0
            if cnt > 0:
                mismatch_items.append((str(item), cnt))
        return mismatch_items, None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


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
    """执行校验并发送通知。返回 {success, message, mismatch_items, error, tv_result, knchat_result}。

    仅当存在异常项（mismatch_cnt > 0）时发送 TV + KN Chat；全部通过则不发送。
    """
    mentions = mentions if mentions is not None else DEFAULT_MENTIONS
    country_name = COUNTRY_NAMES.get(country, country)
    config = get_starrocks_config(sr_password=sr_password, sr_backup_password=sr_backup_password)

    # 1. 执行校验 SQL
    mismatch_items, error = _run_check(config)
    rows = len(mismatch_items) if mismatch_items is not None else None

    message = _build_message(country_name, mismatch_items, error=error)
    print(message)

    tv_result = None
    knchat_result = None
    if dry_run:
        print("（dry-run：不发送任何通知）")
        return {"success": True, "message": message, "rows": rows, "error": error, "tv_result": None, "knchat_result": None}

    # 全部通过且无错误：不发通知，静默返回
    if error is None and rows == 0:
        print("✅ 全部校验项通过，未发送通知")
        return {"success": True, "message": message, "rows": rows, "error": None, "tv_result": None, "knchat_result": None}

    # 2. TV 告警（仅异常/出错时）
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

    # 3. KN Chat 私信/群发（仅异常/出错时）
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
        "mismatch_items": mismatch_items,
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
