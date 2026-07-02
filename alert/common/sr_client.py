#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared StarRocks client helpers for TV alert scripts."""

import os
from dataclasses import dataclass

import pymysql
from pymysql.cursors import DictCursor


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
    fe_host: str = ""
    fe_port: int = 0


def build_config(
    sr_password=None,
    sr_backup_password=None,
    default_host="127.0.0.1",
    default_port=9030,
    default_db="testdb",
    default_username="e_load",
    default_backup_username="backup_user",
    default_fe_host=None,
    default_fe_port=None,
):
    host = os.environ.get("SR_HOST", default_host)
    return StarRocksConfig(
        host=host,
        port=int(os.environ.get("SR_PORT", str(default_port))),
        db=os.environ.get("SR_DB", default_db),
        primary=StarRocksAccount(
            username=os.environ.get("SR_USERNAME", default_username),
            password=sr_password or os.environ.get("SR_PASSWORD", ""),
        ),
        backup=StarRocksAccount(
            username=os.environ.get("SR_BACKUP_USERNAME", default_backup_username),
            password=sr_backup_password or os.environ.get("SR_BACKUP_PASSWORD", ""),
        ),
        fe_host=os.environ.get("SR_FE_HOST", default_fe_host or host),
        fe_port=int(os.environ.get("SR_FE_PORT", str(default_fe_port or 0))),
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


def get_connection(config):
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


def query_all(sql, params=None, config=None):
    conn = get_connection(config)
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return list(cursor.fetchall())
    finally:
        conn.close()


def query_one(sql, params=None, config=None):
    conn = get_connection(config)
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return cursor.fetchone()
    finally:
        conn.close()


def execute_statements(statements, config=None):
    conn = get_connection(config)
    try:
        cursor = conn.cursor()
        for statement in statements:
            cursor.execute(statement)
    finally:
        conn.close()
