import importlib.util
import json
from datetime import date, datetime
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "alert" / "id_marketing_dwd_table_cnt_alert.py"


def load_module():
    spec = importlib.util.spec_from_file_location("id_marketing_dwd_table_cnt_alert", str(MODULE_PATH))
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

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows=None):
        self.cursor_obj = FakeCursor(rows=rows)
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def close(self):
        self.closed = True


class IdMarketingDwdTableCntAlertTests(unittest.TestCase):
    def test_build_refresh_sql_contains_every_expected_dwd_table(self):
        module = load_module()

        sql = module.build_refresh_sql(date(2026, 7, 1))

        self.assertIn("INSERT OVERWRITE testdb.test_dwd_ad_table_cnt_check", sql)
        self.assertIn("'2026-07-01' AS dt", sql)
        for table_name in module.EXPECTED_TABLES:
            self.assertIn(f"from dwd.{table_name}", sql)
            self.assertIn(f"select '{table_name}' as table_name", sql)
        self.assertNotIn("dwd_ad_gg_campaign_unique_users", sql)
        self.assertEqual(sql.count("union all"), len(module.EXPECTED_TABLES) - 1)

    def test_refresh_check_table_creates_table_and_inserts_counts(self):
        module = load_module()
        fake_conn = FakeConnection()
        config = module.StarRocksConfig(
            host="sr-id.example.com",
            port=9030,
            db="testdb",
            primary=module.StarRocksAccount(username="e_load", password="secret"),
            backup=module.StarRocksAccount(username="backup_user", password="backup-secret"),
        )

        with mock.patch.object(module.pymysql, "connect", return_value=fake_conn):
            module.refresh_check_table(target_date=date(2026, 7, 1), config=config)

        self.assertEqual(len(fake_conn.cursor_obj.executed), 2)
        self.assertIn("CREATE TABLE IF NOT EXISTS testdb.test_dwd_ad_table_cnt_check", fake_conn.cursor_obj.executed[0][0])
        self.assertIn("INSERT OVERWRITE testdb.test_dwd_ad_table_cnt_check", fake_conn.cursor_obj.executed[1][0])
        self.assertTrue(fake_conn.closed)

    def test_fetch_check_rows_queries_target_date(self):
        module = load_module()
        rows = [{"table_name": "dwd_ad_tt_report", "cnt": 1}]
        fake_conn = FakeConnection(rows=rows)
        config = module.StarRocksConfig(
            host="sr-id.example.com",
            port=9030,
            db="testdb",
            primary=module.StarRocksAccount(username="e_load", password="secret"),
            backup=module.StarRocksAccount(username="backup_user", password="backup-secret"),
        )

        with mock.patch.object(module.pymysql, "connect", return_value=fake_conn):
            result = module.fetch_check_rows(target_date=date(2026, 7, 1), config=config)

        self.assertEqual(result, rows)
        sql, params = fake_conn.cursor_obj.executed[0]
        self.assertIn("from testdb.test_dwd_ad_table_cnt_check", sql)
        self.assertEqual(params, ("2026-07-01",))
        self.assertTrue(fake_conn.closed)

    def test_find_problem_tables_reports_zero_and_missing_tables(self):
        module = load_module()
        rows = [
            {"table_name": "dwd_ad_gg_conversion_action", "cnt": 0},
            {"table_name": "dwd_ad_gg_placement", "cnt": 12},
        ]

        problems = module.find_problem_tables(
            rows,
            expected_tables=(
                "dwd_ad_gg_conversion_action",
                "dwd_ad_gg_placement",
                "dwd_ad_platform_info",
            ),
        )

        self.assertEqual(
            problems,
            [
                {"table_name": "dwd_ad_gg_conversion_action", "cnt": 0, "reason": "zero_count"},
                {"table_name": "dwd_ad_platform_info", "cnt": None, "reason": "missing_check_result"},
            ],
        )

    def test_format_alert_message_lists_each_problem_table(self):
        module = load_module()
        rows = [
            {
                "dt": date(2026, 7, 1),
                "table_name": table_name,
                "cnt": 10,
                "check_time": datetime(2026, 7, 2, 8, 0, 0),
            }
            for table_name in module.EXPECTED_TABLES
        ]
        rows[0]["cnt"] = 0
        rows[4]["cnt"] = 0

        message = module.format_alert_message(rows, target_date=date(2026, 7, 1))

        self.assertIn("印尼投放DWD表T-1产出校验", message)
        self.assertIn("统计日期: 2026-07-01", message)
        self.assertIn("预期表数: 13，实际校验结果: 13，异常表数: 2", message)
        self.assertIn("1. dwd_ad_gg_conversion_action | cnt=0 | T-1分区数据量为0", message)
        self.assertIn("2. dwd_ad_platform_report_snap | cnt=0 | T-1分区数据量为0", message)

    def test_format_alert_message_outputs_normal_summary_without_problems(self):
        module = load_module()
        rows = [
            {"table_name": table_name, "cnt": 1, "check_time": "2026-07-02 08:00:00"}
            for table_name in module.EXPECTED_TABLES
        ]

        message = module.format_alert_message(rows, target_date=date(2026, 7, 1))

        self.assertIn("异常表数: 0", message)
        self.assertIn("异常明细: 无，所有投放DWD表T-1分区均有数据", message)

    def test_run_refreshes_fetches_and_sends_message(self):
        module = load_module()
        rows = [
            {"table_name": table_name, "cnt": 1, "check_time": "2026-07-02 08:00:00"}
            for table_name in module.EXPECTED_TABLES
        ]

        with mock.patch.object(module, "refresh_check_table") as refresh:
            with mock.patch.object(module, "fetch_check_rows", return_value=rows) as fetch:
                with mock.patch.object(
                    module,
                    "send_to_tv",
                    return_value={"success": True, "status_code": 200, "response": "ok"},
                ) as send:
                    with mock.patch("builtins.print"):
                        result = module.run(
                            target_date=date(2026, 7, 1),
                            mentions=["owner@kn.group"],
                            sr_password="primary-secret",
                            sr_backup_password="backup-secret",
                            bot_id="bot-1",
                        )

        self.assertTrue(result["success"])
        refresh.assert_called_once()
        fetch.assert_called_once()
        self.assertIn("印尼投放DWD表T-1产出校验", send.call_args.args[0])
        self.assertEqual(send.call_args.kwargs["mentions"], ["owner@kn.group"])
        self.assertEqual(send.call_args.kwargs["bot_id"], "bot-1")

    def test_send_to_tv_uses_mentions_payload(self):
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


if __name__ == "__main__":
    unittest.main()
