import importlib.util
import json
from datetime import date
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "alert" / "mx_capital_ltv_alert.py"
REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module():
    spec = importlib.util.spec_from_file_location("mx_capital_ltv_alert", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    fake_pymysql = types.SimpleNamespace(
        connect=mock.Mock(),
        cursors=types.SimpleNamespace(DictCursor=object),
    )
    with mock.patch.dict(
        sys.modules,
        {
            "pymysql": fake_pymysql,
            "pymysql.cursors": fake_pymysql.cursors,
        },
    ):
        spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, status_code, body):
        self._status_code = status_code
        self._body = body

    def getcode(self):
        return self._status_code

    def read(self):
        return self._body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []
        self.fetchone_index = 0

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        if self.fetchone_index >= len(self.rows):
            return None
        row = self.rows[self.fetchone_index]
        self.fetchone_index += 1
        return row


class FakeConnection:
    def __init__(self, rows=None):
        self.cursor_obj = FakeCursor(rows=rows)
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def close(self):
        self.closed = True


class MxCapitalLtvAlertTests(unittest.TestCase):
    def test_fetch_capital_ltv_rows_runs_two_latest_record_queries_for_two_capitals(self):
        module = load_module()
        fake_conn = FakeConnection(
            [
                {
                    "stat_date": date(2026, 6, 21),
                    "capital": "new_share",
                    "ltv": 0.66,
                    "normal_loan_amt_peso": 123,
                    "normal_loan_amt_usd": 6.15,
                    "account_balance_peso": 4420001,
                    "account_balance_usd": 221000.05,
                    "exchange_usd_rate": 20,
                },
                {
                    "stat_date": date(2026, 6, 21),
                    "capital": "chuanjin",
                    "ltv": 1.5,
                    "normal_loan_amt_peso": 456,
                    "normal_loan_amt_usd": 22.8,
                    "account_balance_peso": 424001,
                    "account_balance_usd": 21200.05,
                    "exchange_usd_rate": 20,
                }
            ]
        )
        config = module.StarRocksConfig(
            host="sr-mx.example.com",
            port=9030,
            db="dm_dd_new",
            primary=module.StarRocksAccount(username="e_load", password="secret"),
            backup=module.StarRocksAccount(username="backup_user", password="backup-secret"),
        )

        with mock.patch.object(module.pymysql, "connect", return_value=fake_conn):
            rows = module.fetch_capital_ltv_rows(target_date=date(2026, 6, 21), config=config)

        self.assertEqual(len(rows), 2)
        self.assertEqual(len(fake_conn.cursor_obj.executed), 2)
        sql, params = fake_conn.cursor_obj.executed[0]
        self.assertIn("from dm_dd_new.ads_capital_ltv", sql)
        self.assertIn("inner join dim.dim_currency_rate", sql)
        self.assertIn("a.normal_loan_amt / b.exchange_usd_rate as normal_loan_amt_usd", sql)
        self.assertIn("a.account_balance / b.exchange_usd_rate as account_balance_usd", sql)
        self.assertIn("b.exchange_usd_rate", sql)
        self.assertIn("stat_date >= %s", sql)
        self.assertIn("a.capital = %s", sql)
        self.assertIn("b.currency_code = 'MXN'", sql)
        self.assertIn("order by a.stat_date desc", sql)
        self.assertIn("limit 1", sql)
        self.assertNotIn("field(", sql.lower())
        self.assertEqual(params, ("2026-05-01", "new_share"))
        self.assertEqual(fake_conn.cursor_obj.executed[1][1], ("2026-05-01", "chuanjin"))
        self.assertTrue(fake_conn.closed)

    def test_format_alert_message_includes_new_share_qualified_and_balance_tag(self):
        module = load_module()
        rows = [
            {
                "stat_date": date(2026, 6, 21),
                "capital": "new_share",
                "ltv": 0.66,
                "normal_loan_amt_peso": 1000000,
                "normal_loan_amt_usd": 50000,
                "account_balance_peso": 4420001,
                "account_balance_usd": 221000.05,
                "exchange_usd_rate": 20,
            }
        ]

        message = module.format_alert_message(rows, target_date=date(2026, 6, 21))

        self.assertIn("墨西哥资方ltv告警", message)
        self.assertIn("统计日: 2026-06-21", message)
        self.assertLess(message.index("告警时间:"), message.index("依赖任务:"))
        self.assertLess(message.index("依赖任务:"), message.index("告警项: 墨西哥新分享ltv"))
        self.assertIn("告警项: 墨西哥新分享ltv", message)
        self.assertIn("信托账户余额: 4,420,001 比索，即 221,000.05 美元", message)
        self.assertIn("质押正常在贷: 1,000,000 比索，即 50,000 美元", message)
        self.assertIn("ltv值: 0.66", message)
        self.assertIn("兑美元汇率: 20", message)
        self.assertIn("在阈值0.75以下，在合格线", message)
        self.assertIn("通道余额大于44,200,00，需关注", message)
        self.assertIn("告警项: 墨西哥串金ltv", message)
        self.assertIn("通道余额: 未查询到", message)
        self.assertIn("ltv值: 未查询到", message)
        self.assertIn("附加标签: 未查询到该资方 LTV 数据，需检查 dm_dd_new.ads_capital_ltv 产出", message)

    def test_format_alert_message_includes_chuanjin_emergency_and_reduction_watch_tags(self):
        module = load_module()

        emergency = module.format_alert_message(
            [
                {
                    "stat_date": "2026-06-21",
                    "capital": "chuanjin",
                    "ltv": 1.42,
                    "normal_loan_amt_peso": 200000,
                    "normal_loan_amt_usd": 10000,
                    "account_balance_peso": 424001,
                    "account_balance_usd": 21200.05,
                    "exchange_usd_rate": 20,
                }
            ],
            target_date=date(2026, 6, 21),
        )
        reduction_watch = module.format_alert_message(
            [
                {
                    "stat_date": "2026-06-21",
                    "capital": "chuanjin",
                    "ltv": 1.9,
                    "normal_loan_amt_peso": 200000,
                    "normal_loan_amt_usd": 10000,
                    "account_balance_peso": 1,
                    "account_balance_usd": 0.05,
                    "exchange_usd_rate": 20,
                }
            ],
            target_date=date(2026, 6, 21),
        )

        self.assertIn("告警项: 墨西哥串金ltv", emergency)
        self.assertIn("通道余额: 424,001 比索，即 21,200.05 美元", emergency)
        self.assertIn("兑美元汇率: 20", emergency)
        self.assertIn("在阈值1.43以下，需紧急介入线", emergency)
        self.assertIn("通道余额大于424,000，需关注", emergency)
        self.assertIn("在阈值1.43以上，但需关注通道余额或者资产，是否需要减持", reduction_watch)
        self.assertNotIn("通道余额大于424,000", reduction_watch)

    def test_format_alert_message_always_outputs_two_capital_sections(self):
        module = load_module()

        message = module.format_alert_message([], target_date=date(2026, 6, 21))

        self.assertIn("告警项: 墨西哥新分享ltv", message)
        self.assertIn("信托账户余额: 未查询到", message)
        self.assertIn("告警项: 墨西哥串金ltv", message)
        self.assertIn("通道余额: 未查询到", message)
        self.assertEqual(message.count("附加标签:"), 2)

    def test_format_alert_message_can_output_only_requested_capital(self):
        module = load_module()

        message = module.format_alert_message([], target_date=date(2026, 6, 21), capitals=["chuanjin"])

        self.assertNotIn("告警项: 墨西哥新分享ltv", message)
        self.assertIn("告警项: 墨西哥串金ltv", message)
        self.assertEqual(message.count("告警项:"), 1)

    def test_run_sends_formatted_message_to_tv(self):
        module = load_module()
        rows = [
            {
                "stat_date": "2026-06-21",
                "capital": "new_share",
                "ltv": 0.76,
                "normal_loan_amt_peso": 100,
                "normal_loan_amt_usd": 5,
                "account_balance_peso": 10,
                "account_balance_usd": 0.5,
                "exchange_usd_rate": 20,
            }
        ]

        with mock.patch.object(module, "fetch_capital_ltv_rows", return_value=rows) as fetch:
            with mock.patch.object(
                module,
                "send_to_tv",
                return_value={"success": True, "status_code": 200, "response": "ok"},
            ) as send:
                with mock.patch("builtins.print"):
                    result = module.run(
                        target_date=date(2026, 6, 21),
                        mentions=["owner@kn.group"],
                        sr_password="primary-secret",
                        sr_backup_password="backup-secret",
                        bot_id="bot-1",
                    )

        self.assertTrue(result["success"])
        fetch.assert_called_once()
        message = send.call_args.args[0]
        self.assertIn("在阈值0.75以上，需紧急介入", message)
        self.assertTrue(message.endswith("\n"))
        self.assertEqual(send.call_args.kwargs["mentions"], ["owner@kn.group"])
        self.assertEqual(send.call_args.kwargs["bot_id"], "bot-1")

    def test_run_can_send_only_requested_capital_message(self):
        module = load_module()
        rows = [
            {
                "stat_date": "2026-06-21",
                "capital": "chuanjin",
                "ltv": 1.5,
                "normal_loan_amt_peso": 100,
                "normal_loan_amt_usd": 5,
                "account_balance_peso": 424001,
                "account_balance_usd": 21200.05,
                "exchange_usd_rate": 20,
            }
        ]

        with mock.patch.object(module, "fetch_capital_ltv_rows", return_value=rows) as fetch:
            with mock.patch.object(
                module,
                "send_to_tv",
                return_value={"success": True, "status_code": 200, "response": "ok"},
            ) as send:
                with mock.patch("builtins.print"):
                    result = module.run(
                        capital="chuanjin",
                        mentions=["owner@kn.group"],
                        sr_password="primary-secret",
                        sr_backup_password="backup-secret",
                        bot_id="bot-1",
                    )

        self.assertTrue(result["success"])
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.kwargs["capital"], "chuanjin")
        message = send.call_args.args[0]
        self.assertNotIn("墨西哥新分享ltv", message)
        self.assertIn("墨西哥串金ltv", message)

    def test_send_to_tv_uses_requested_bot_and_mentions_field(self):
        module = load_module()
        captured = {}

        def fake_urlopen(request, timeout=0):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse(200, '{"ok":true}')

        with mock.patch.object(module.urllib.request, "urlopen", side_effect=fake_urlopen):
            result = module.send_to_tv("告警内容", mentions=["owner@kn.group"], bot_id="bot-1")

        self.assertTrue(result["success"])
        self.assertEqual(captured["timeout"], 30)
        self.assertEqual(
            captured["body"],
            {
                "botId": "bot-1",
                "message": "告警内容\n",
                "mentions": ["owner@kn.group"],
            },
        )

    def test_send_to_tv_uses_mx_ltv_bot_by_default(self):
        module = load_module()
        captured = {}

        def fake_urlopen(request, timeout=0):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse(200, '{"ok":true}')

        with mock.patch.object(module.urllib.request, "urlopen", side_effect=fake_urlopen):
            result = module.send_to_tv("告警内容", mentions=["owner@kn.group"])

        self.assertTrue(result["success"])
        self.assertEqual(captured["body"]["botId"], "5d0be3c3-0e06-4134-bbbe-690d7ff28d1e")

    def test_main_passes_cli_arguments_to_run(self):
        module = load_module()
        captured = {}

        def fake_run(
            dry_run=False,
            mentions=None,
            sr_password=None,
            sr_backup_password=None,
            bot_id=None,
            target_date=None,
            capital=None,
        ):
            captured["dry_run"] = dry_run
            captured["mentions"] = mentions
            captured["sr_password"] = sr_password
            captured["sr_backup_password"] = sr_backup_password
            captured["bot_id"] = bot_id
            captured["target_date"] = target_date
            captured["capital"] = capital
            return {"success": True, "status_code": None, "response": "ok"}

        with mock.patch.object(module, "run", side_effect=fake_run):
            exit_code = module.main(
                [
                    "--dry-run",
                    "--target-date",
                    "2026-06-21",
                    "--sr-password",
                    "primary-secret",
                    "--sr-backup-password",
                    "backup-secret",
                    "--bot-id",
                    "bot-1",
                    "--capital",
                    "chuanjin",
                    "--mentions",
                    "owner@kn.group,backup@kn.group",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertTrue(captured["dry_run"])
        self.assertEqual(captured["mentions"], ["owner@kn.group", "backup@kn.group"])
        self.assertEqual(captured["sr_password"], "primary-secret")
        self.assertEqual(captured["sr_backup_password"], "backup-secret")
        self.assertEqual(captured["bot_id"], "bot-1")
        self.assertEqual(captured["target_date"], date(2026, 6, 21))
        self.assertEqual(captured["capital"], "chuanjin")

    def test_obsolete_split_mx_ltv_n8n_workflows_are_removed(self):
        self.assertFalse((REPO_ROOT / "n8n/mx_new_share_ltv_alert_workflow.json").exists())
        self.assertFalse((REPO_ROOT / "n8n/mx_chuanjin_ltv_alert_workflow.json").exists())


if __name__ == "__main__":
    unittest.main()
