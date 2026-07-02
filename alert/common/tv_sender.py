#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared TV alert sender."""

import json
import os
import urllib.error
import urllib.request


TV_API_URL = os.environ.get(
    "TV_API_URL",
    "https://tv-service-alert.kuainiu.chat/alert/v2/array",
)


def send_to_tv(message, mentions=None, bot_id=None, api_url=None):
    if mentions is None:
        mentions = []
    if not message.endswith("\n"):
        message = f"{message}\n"

    payload = {
        "botId": bot_id,
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
