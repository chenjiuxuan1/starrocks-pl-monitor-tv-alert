#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared KN Chat bot sender (private message / group message).

API: https://kn.chat/docs/bot/  —  Bot API Base: https://bot.kn.chat
  sendMessage: POST https://bot.kn.chat/bot<TOKEN>/sendMessage  {"chat_id": ..., "text": ...}

Token 建议通过环境变量 KNCHAT_BOT_TOKEN 提供，避免硬编码进脚本/命令。
"""

import json
import os
import urllib.error
import urllib.request

KNCHAT_API_BASE = os.environ.get(
    "KNCHAT_API_BASE",
    "https://bot.kn.chat",
)
DEFAULT_KNCHAT_BOT_TOKEN = os.environ.get("KNCHAT_BOT_TOKEN", "")


def _url(token, method):
    token = token or DEFAULT_KNCHAT_BOT_TOKEN
    if not token:
        raise ValueError("缺少 knchat bot token（传 token 或设置环境变量 KNCHAT_BOT_TOKEN）")
    return f"{KNCHAT_API_BASE}/bot{token}/{method}"


def get_me(token=None):
    """验证 token 并获取机器人基础信息。"""
    request = urllib.request.Request(_url(token, "getMe"), headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def send_message(chat_id, text, token=None, parse_mode=None, disable_web_page_preview=False, reply_to_message_id=None):
    """向私聊 / 普通群 / 超级群发送文本消息。

    返回 {ok, error_code, description, result}。
    """
    payload = {
        "chat_id": chat_id,
        "text": text,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if disable_web_page_preview:
        payload["disable_web_page_preview"] = True
    if reply_to_message_id is not None:
        payload["reply_to_message_id"] = reply_to_message_id

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        _url(token, "sendMessage"),
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.loads(response.read().decode("utf-8"))
            return {
                "success": bool(body.get("ok")),
                "ok": bool(body.get("ok")),
                "error_code": body.get("error_code"),
                "description": body.get("description", ""),
                "result": body.get("result"),
            }
    except urllib.error.HTTPError as exc:
        description = ""
        if exc.fp is not None:
            try:
                description = exc.fp.read().decode("utf-8")
            except Exception:
                description = ""
        return {
            "success": False,
            "ok": False,
            "error_code": exc.code,
            "description": description or str(exc.reason),
            "result": None,
        }
    except Exception as exc:
        return {
            "success": False,
            "ok": False,
            "error_code": None,
            "description": str(exc),
            "result": None,
        }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="发送一条 KN Chat 私信/群消息（测试用）")
    parser.add_argument("--chat-id", required=True, help="目标会话 ID（群或私聊）")
    parser.add_argument("--text", required=True, help="消息文本")
    parser.add_argument("--token", default=None, help="机器人 token；缺省读环境变量 KNCHAT_BOT_TOKEN")
    args = parser.parse_args()

    result = send_message(args.chat_id, args.text, token=args.token)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("success"):
        raise SystemExit(1)
