import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "alert" / "fin_manage_ods_data_quality_monitor_alert.py"


def load_module():
    spec = importlib.util.spec_from_file_location("fin_manage_ods_data_quality_monitor_alert", str(MODULE_PATH))
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
    def __init__(self, rows=None, one=None):
        self.rows = rows or []
        self.one = one
        self.fetchone_index = 0
        self.executed_sqls = []

    def execute(self, sql):
        self.executed_sqls.append(sql)

    def fetchall(self):
        return self.rows

    def fetchone(self):
        if self.one is None and self.fetchone_index < len(self.rows):
            row = self.rows[self.fetchone_index]
            self.fetchone_index += 1
            return row
        return self.one


class FakeConnection:
    def __init__(self, rows=None, one=None):
        self.cursor_obj = FakeCursor(rows=rows, one=one)
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def close(self):
        self.closed = True


class FinManageOdsDataQualityMonitorAlertTests(unittest.TestCase):
    def test_default_mentions_include_gretchenhe(self):
        module = load_module()

        self.assertEqual(module.DEFAULT_MENTIONS, ["adamyu@kn.group", "gretchenhe@kn.group"])

    def test_fin_sql_counts_capital_three_tables_and_abs_diff(self):
        module = load_module()
        total_sql = module.LATEST_BATCH_TOTAL_COUNT_SQL.lower()
        exc_sql = module.LATEST_BATCH_EXCEPTION_COUNT_SQL.lower()
        self.assertIn("select count(1) as alert_count", total_sql)
        self.assertIn("bi_collection_report", total_sql)
        self.assertIn("bi_report_apportion_before", total_sql)
        self.assertIn("bi_report_apportion_after", total_sql)
        # 财务库告警只统计 ods_security. 开头的 capital 三表
        self.assertIn("ods_capital_bi_collection_report", total_sql)
        self.assertIn("ods_capital_bi_report_apportion_before", total_sql)
        self.assertIn("ods_capital_bi_report_apportion_after", total_sql)
        # 不再查监控表 dt = max(dt)，不再用 diff <> 0
        self.assertNotIn("dt = (select max(dt)", total_sql)
        self.assertNotIn("diff <> 0", exc_sql)
        # 异常：abs(diff) > 1
        self.assertIn("where abs(__t.diff) > 1", exc_sql)
        # 必须为当前日期，不再使用 @v_start_date 会话变量
        self.assertNotIn("@v_start_date", total_sql)
        self.assertIn("current_date()", total_sql)

    def test_biz_total_sql_counts_nonoperate_rows_without_diff_filter(self):
        module = load_module()
        sql = module.BIZ_LATEST_BATCH_TOTAL_COUNT_SQL.lower()
        self.assertIn("select count(1) as alert_count", sql)
        self.assertIn("from (", sql)
        # biz库只统计 fin_global. 开头的五国非经营费用
        self.assertIn("ods_pk_pl_nonoperate_expense_monthly", sql)
        self.assertIn("ods_mx_pl_nonoperate_expense_monthly", sql)
        self.assertIn("ods_ph_pl_nonoperate_expense_monthly", sql)
        self.assertIn("ods_th_pl_nonoperate_expense_monthly", sql)
        self.assertIn("ods_ine_pl_nonoperate_expense_monthly", sql)
        self.assertIn("pl_nonoperate_expense_monthly_global", sql)
        self.assertNotIn("abs(", sql)
        self.assertNotIn("manage_model_pl_operational_cost_apportion_global", sql)

    def test_biz_exception_sql_filters_abs_diff_greater_than_one(self):
        module = load_module()
        sql = module.BIZ_LATEST_BATCH_EXCEPTION_COUNT_SQL.lower()
        self.assertIn("select count(1) as alert_count", sql)
        self.assertIn("where abs(__t.diff) > 1", sql)
        self.assertIn("ods_pk_pl_nonoperate_expense_monthly", sql)
        self.assertIn("ods_mx_pl_nonoperate_expense_monthly", sql)
        self.assertIn("ods_ph_pl_nonoperate_expense_monthly", sql)
        self.assertIn("ods_th_pl_nonoperate_expense_monthly", sql)
        self.assertIn("ods_ine_pl_nonoperate_expense_monthly", sql)
        self.assertIn("pl_nonoperate_expense_monthly_global", sql)
        # biz库不再包含 capital 三表与分摊 global 对账
        self.assertNotIn("ods_capital_bi_collection_report", sql)
        self.assertNotIn("bi_report_apportion_before", sql)
        self.assertNotIn("bi_report_apportion_after", sql)
        self.assertNotIn("manage_model_pl_operational_cost_apportion_global", sql)
        self.assertNotIn("self_owned_fund_income", sql)
        # 必须为当前日期，不再使用 @v_start_date 会话变量
        self.assertNotIn("@v_start_date", sql)
        self.assertIn("current_date()", sql)

    def test_biz_monitor_table_points_to_fin_global_nonoperate(self):
        module = load_module()
        self.assertIn("fin_global", module.BIZ_MONITOR_TABLE)
        self.assertIn("非经营", module.BIZ_MONITOR_TABLE)
        self.assertNotEqual(module.BIZ_MONITOR_TABLE, "fin.fin_manage_ods_data_quality_monitor")

    def test_fetch_latest_batch_counts_counts_all_and_diff_rows(self):
        module = load_module()
        fake_conn = FakeConnection(rows=[{"alert_count": 172326}, {"alert_count": 834}])
        config = module.StarRocksConfig(
            host="sr.example.com",
            port=9031,
            fe_host="sr.example.com",
            fe_port=8031,
            db="fin",
            primary=module.StarRocksAccount(username="e_load", password="secret"),
            backup=module.StarRocksAccount(username="e_backup", password="backup-secret"),
        )

        with mock.patch.object(module.pymysql, "connect", return_value=fake_conn):
            counts = module.fetch_latest_batch_counts(config=config)

        self.assertEqual(counts, {"alert_count": 172326, "exception_count": 834})
        self.assertEqual(len(fake_conn.cursor_obj.executed_sqls), 2)
        self.assertEqual(fake_conn.cursor_obj.executed_sqls[0], module.LATEST_BATCH_TOTAL_COUNT_SQL)
        self.assertEqual(fake_conn.cursor_obj.executed_sqls[1], module.LATEST_BATCH_EXCEPTION_COUNT_SQL)
        self.assertTrue(fake_conn.closed)

    def test_fetch_biz_latest_batch_counts_counts_all_and_diff_rows(self):
        module = load_module()
        fake_conn = FakeConnection(rows=[{"alert_count": 40462}, {"alert_count": 3}])
        config = module.StarRocksConfig(
            host="sr.example.com",
            port=9031,
            fe_host="sr.example.com",
            fe_port=8031,
            db="fin",
            primary=module.StarRocksAccount(username="e_load", password="secret"),
            backup=module.StarRocksAccount(username="e_backup", password="backup-secret"),
        )

        with mock.patch.object(module.pymysql, "connect", return_value=fake_conn):
            counts = module.fetch_biz_latest_batch_counts(config=config)

        self.assertEqual(counts, {"alert_count": 40462, "exception_count": 3})
        self.assertEqual(fake_conn.cursor_obj.executed_sqls[0], module.BIZ_LATEST_BATCH_TOTAL_COUNT_SQL)
        self.assertEqual(fake_conn.cursor_obj.executed_sqls[1], module.BIZ_LATEST_BATCH_EXCEPTION_COUNT_SQL)
        self.assertTrue(fake_conn.closed)

    def test_format_fin_alert_message_matches_summary_style(self):
        module = load_module()

        message = module.format_fin_alert_message(alert_count=172326, exception_count=834)

        self.assertIn("🚨 StarRocks 数仓与财务库数据一致性校验", message)
        self.assertIn("集群: 中国", message)
        self.assertIn("告警记录: 172326 条，异常告警：834条，", message)
        self.assertIn("查询表: ods_security.ods_capital_bi_*", message)
        self.assertNotIn("select count(1)", message)

    def test_format_biz_alert_message_matches_summary_style(self):
        module = load_module()

        message = module.format_biz_alert_message(alert_count=40462, exception_count=0)

        self.assertIn("🚨 StarRocks 数仓与biz库数据一致性校验", message)
        self.assertIn("集群: 中国", message)
        self.assertIn("告警记录: 40462 条，异常告警：0条，", message)
        self.assertIn("查询表: fin_global.ods_*_pl_nonoperate_expense_monthly", message)
        self.assertNotIn("select count(1)", message)

    def test_send_to_tv_uses_requested_bot_and_mentions_field(self):
        module = load_module()
        captured = {}

        def fake_urlopen(request, timeout=0):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse(200, '{"ok":true}')

        with mock.patch.object(module.urllib.request, "urlopen", side_effect=fake_urlopen):
            result = module.send_to_tv(
                "告警内容",
                mentions=["strongliu@kn.group"],
                bot_id="4d0bcc9b-71bf-41c5-ba9f-89b7278f9214",
            )

        self.assertTrue(result["success"])
        self.assertEqual(captured["url"], module.TV_API_URL)
        self.assertEqual(captured["timeout"], 30)
        self.assertEqual(
            captured["body"],
            {
                "botId": "4d0bcc9b-71bf-41c5-ba9f-89b7278f9214",
                "message": "告警内容\n",
                "mentions": ["strongliu@kn.group"],
            },
        )

    def test_send_to_tv_falls_back_to_default_mentions_when_empty_list(self):
        module = load_module()

        with mock.patch.object(module, "tv_sender") as fake_tv:
            fake_tv.send_to_tv.return_value = {"success": True, "status_code": 200, "response": "ok"}
            result = module.send_to_tv("告警内容", mentions=[])

        self.assertTrue(result["success"])
        # run_alert.py 传空列表时也应回退到默认提醒人（含 gretchenhe）
        self.assertEqual(
            fake_tv.send_to_tv.call_args.kwargs["mentions"],
            ["adamyu@kn.group", "gretchenhe@kn.group"],
        )

    def test_run_sends_both_fin_and_biz_messages_and_falls_back_mentions(self):
        module = load_module()
        fake_conn = FakeConnection(rows=[{"alert_count": 172326}, {"alert_count": 834}])
        config = module.StarRocksConfig(
            host="sr.example.com",
            port=9031,
            fe_host="sr.example.com",
            fe_port=8031,
            db="fin",
            primary=module.StarRocksAccount(username="e_load", password="secret"),
            backup=module.StarRocksAccount(username="e_backup", password="backup-secret"),
        )

        with mock.patch.object(module, "get_connection", return_value=fake_conn):
            with mock.patch.object(module, "tv_sender") as fake_tv:
                fake_tv.send_to_tv.return_value = {"success": True, "status_code": 200, "response": "ok"}
                with mock.patch("builtins.print"):
                    result = module.run(
                        mentions=[],
                        sr_password="primary-secret",
                        sr_backup_password="backup-secret",
                        bot_id="4d0bcc9b-71bf-41c5-ba9f-89b7278f9214",
                    )

        self.assertTrue(result["success"])
        # 一次运行：财务库 + biz库 各一条消息，共两次 TV 发送
        self.assertEqual(fake_tv.send_to_tv.call_count, 2)
        first_message = fake_tv.send_to_tv.call_args_list[0].args[0]
        second_message = fake_tv.send_to_tv.call_args_list[1].args[0]
        self.assertIn("🚨 StarRocks 数仓与财务库数据一致性校验", first_message)
        self.assertIn("🚨 StarRocks 数仓与biz库数据一致性校验", second_message)
        self.assertTrue(first_message.endswith("\n"))
        self.assertTrue(second_message.endswith("\n"))
        # 空 mentions 经 send_to_tv 回退到默认提醒人
        for call in fake_tv.send_to_tv.call_args_list:
            self.assertEqual(call.kwargs["mentions"], ["adamyu@kn.group", "gretchenhe@kn.group"])
            self.assertEqual(call.kwargs["bot_id"], "4d0bcc9b-71bf-41c5-ba9f-89b7278f9214")

    def test_main_passes_starrocks_passwords_from_command_line(self):
        module = load_module()
        captured = {}

        def fake_run(limit, dry_run, mentions, sr_password=None, sr_backup_password=None, bot_id=None):
            captured["limit"] = limit
            captured["dry_run"] = dry_run
            captured["mentions"] = mentions
            captured["sr_password"] = sr_password
            captured["sr_backup_password"] = sr_backup_password
            captured["bot_id"] = bot_id
            return {"success": True, "status_code": None, "response": "ok"}

        with mock.patch.object(module, "run", side_effect=fake_run):
            exit_code = module.main(
                [
                    "--sr-password",
                    "primary-secret",
                    "--sr-backup-password",
                    "backup-secret",
                    "--limit",
                    "10",
                    "--bot-id",
                    "4d0bcc9b-71bf-41c5-ba9f-89b7278f9214",
                    "--mentions",
                    "strongliu@kn.group,jerrycai@kn.group",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(captured["limit"], 10)
        self.assertFalse(captured["dry_run"])
        self.assertEqual(captured["mentions"], ["strongliu@kn.group", "jerrycai@kn.group"])
        self.assertEqual(captured["sr_password"], "primary-secret")
        self.assertEqual(captured["sr_backup_password"], "backup-secret")
        self.assertEqual(captured["bot_id"], "4d0bcc9b-71bf-41c5-ba9f-89b7278f9214")


if __name__ == "__main__":
    unittest.main()
