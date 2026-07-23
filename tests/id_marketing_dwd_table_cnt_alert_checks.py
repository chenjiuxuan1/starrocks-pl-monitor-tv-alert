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
    def test_build_check_config_rejects_unsafe_table_identifier(self):
        module = load_module()

        with self.assertRaises(ValueError):
            module.build_check_config(table_names="dwd_ad_tt_report;drop table x")

    def test_build_check_config_selects_country_profile(self):
        module = load_module()

        check_config = module.build_check_config(profile="ph")

        self.assertEqual(check_config.profile, "ph")
        self.assertEqual(check_config.country_code, "ph")
        self.assertEqual(check_config.country_name, "菲律宾")
        self.assertEqual(check_config.platform_type, "投放")
        self.assertEqual(check_config.check_table, "testdb.test_dwd_ad_table_cnt_check")
        self.assertEqual(check_config.expected_tables, module.EXPECTED_TABLES)

    def test_build_check_config_selects_mx_profile_without_canceled_tables(self):
        module = load_module()

        check_config = module.build_check_config(profile="mx")

        self.assertEqual(check_config.country_name, "墨西哥")
        self.assertEqual(check_config.country_code, "mx")
        self.assertEqual(len(check_config.expected_tables), 16)
        self.assertIn("dwd_ad_gg_campaign_unique_users", check_config.expected_tables)
        self.assertIn("dwd_ad_tt_report_placement", check_config.expected_tables)
        self.assertNotIn("dwd_ad_platform_report_full", check_config.expected_tables)
        self.assertNotIn("dwd_ad_platform_report_snap", check_config.expected_tables)

    def test_build_check_config_allows_platform_type_override(self):
        module = load_module()

        check_config = module.build_check_config(profile="th", platform_type="广告投放")

        self.assertEqual(check_config.country_name, "泰国")
        self.assertEqual(check_config.platform_type, "广告投放")
        self.assertEqual(check_config.alert_title, "🚨 泰国广告投放DWD表T-1产出校验")

    def test_build_check_config_rejects_unknown_profile(self):
        module = load_module()

        with self.assertRaises(ValueError):
            module.build_check_config(profile="unknown-country")

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
        self.assertIn("country_code = %s", sql)
        self.assertIn("platform_type = %s", sql)
        self.assertEqual(params, ("2026-07-01", "id", "投放"))
        self.assertTrue(fake_conn.closed)

    def test_fetch_check_rows_uses_custom_check_table(self):
        module = load_module()
        fake_conn = FakeConnection(rows=[])
        config = module.StarRocksConfig(
            host="sr-ph.example.com",
            port=9030,
            db="testdb",
            primary=module.StarRocksAccount(username="e_load", password="secret"),
            backup=module.StarRocksAccount(username="backup_user", password="backup-secret"),
        )
        check_config = module.MarketingDwdCheckConfig(
            country_name="菲律宾",
            check_table="testdb.ph_dwd_ad_table_cnt_check",
            expected_tables=module.EXPECTED_TABLES,
        )

        with mock.patch.object(module.pymysql, "connect", return_value=fake_conn):
            module.fetch_check_rows(target_date=date(2026, 7, 1), config=config, check_config=check_config)

        sql, _ = fake_conn.cursor_obj.executed[0]
        self.assertIn("from testdb.ph_dwd_ad_table_cnt_check", sql)

    def test_find_problem_tables_reports_zero_rows_from_check_table(self):
        module = load_module()
        rows = [
            {"table_name": "dwd_ad_gg_conversion_action", "cnt": 0},
            {"table_name": "dwd_ad_gg_placement", "cnt": 12},
            {"table_name": "dwd_ad_fb_ad_set_get", "cnt": 0},
        ]

        problems = module.find_problem_tables(rows)

        self.assertEqual(
            problems,
            [
                {"table_name": "dwd_ad_gg_conversion_action", "cnt": 0, "reason": "zero_count"},
                {"table_name": "dwd_ad_fb_ad_set_get", "cnt": 0, "reason": "zero_count"},
            ],
        )

    def test_mx_profile_ignores_retired_dedup_check_row(self):
        module = load_module()
        check_config = module.build_check_config(profile="mx")
        rows = [
            {"table_name": "dwd_ad_tt_report", "cnt": 12},
            {"table_name": "dwd_ad_fb_ad_insight_impression_age_gender_dedup", "cnt": 0},
        ]

        problems = module.find_problem_tables(rows, ignored_tables=check_config.ignored_tables)
        message = module.format_alert_message(
            rows,
            target_date=date(2026, 7, 21),
            check_config=check_config,
        )

        self.assertEqual(problems, [])
        self.assertIn("校验结果表数: 1，异常表数: 0", message)
        self.assertNotIn("dwd_ad_fb_ad_insight_impression_age_gender_dedup", message)

    def test_pk_profile_ignores_t_minus_2_table_in_t_minus_1_alert(self):
        module = load_module()
        check_config = module.build_check_config(profile="pk")
        rows = [
            {"table_name": "dwd_ad_tt_campaign_get", "cnt": 170},
            {"table_name": "dwd_ad_tt_report", "cnt": 0},
        ]

        problems = module.find_problem_tables(rows, ignored_tables=check_config.ignored_tables)
        message = module.format_alert_message(
            rows,
            target_date=date(2026, 7, 22),
            check_config=check_config,
        )

        self.assertEqual(problems, [])
        self.assertIn("校验结果表数: 1，异常表数: 0", message)
        self.assertNotIn("dwd_ad_tt_report", message)

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
        self.assertIn("校验结果表数: 13，异常表数: 2", message)
        self.assertIn("1. dwd_ad_gg_conversion_action | cnt=0 | 2026-07-01 数据量为0，数据有问题", message)
        self.assertIn("2. dwd_ad_platform_report_snap | cnt=0 | 2026-07-01 数据量为0，数据有问题", message)

    def test_format_alert_message_uses_rows_from_check_table_for_custom_country(self):
        module = load_module()
        check_config = module.MarketingDwdCheckConfig(
            country_name="泰国",
            check_table="testdb.th_dwd_ad_table_cnt_check",
            expected_tables=("dwd_ad_tt_report", "dwd_ad_tt_campaign_get"),
        )
        rows = [
            {
                "dt": date(2026, 7, 1),
                "table_name": "dwd_ad_tt_report",
                "cnt": 0,
                "check_time": datetime(2026, 7, 2, 8, 0, 0),
            }
        ]

        message = module.format_alert_message(rows, target_date=date(2026, 7, 1), check_config=check_config)

        self.assertIn("泰国投放DWD表T-1产出校验", message)
        self.assertIn("校验表: testdb.th_dwd_ad_table_cnt_check", message)
        self.assertIn("校验结果表数: 1，异常表数: 1", message)
        self.assertIn("dwd_ad_tt_report | cnt=0 | 2026-07-01 数据量为0，数据有问题", message)

    def test_format_alert_message_outputs_normal_summary_without_problems(self):
        module = load_module()
        rows = [
            {"table_name": table_name, "cnt": 1, "check_time": "2026-07-02 08:00:00"}
            for table_name in module.EXPECTED_TABLES
        ]

        message = module.format_alert_message(rows, target_date=date(2026, 7, 1))

        self.assertIn("异常表数: 0", message)
        self.assertIn("异常明细: 无，所有投放DWD表T-1分区均有数据", message)

    def test_run_reads_check_table_and_sends_message_by_default(self):
        module = load_module()
        rows = [
            {"table_name": table_name, "cnt": 1, "check_time": "2026-07-02 08:00:00"}
            for table_name in module.EXPECTED_TABLES
        ]
        rows[0]["cnt"] = 0

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
                        country_name="菲律宾",
                        check_table="testdb.ph_dwd_ad_table_cnt_check",
                    )

        self.assertTrue(result["success"])
        fetch.assert_called_once()
        self.assertIn("菲律宾投放DWD表T-1产出校验", send.call_args.args[0])
        self.assertIn("校验表: testdb.ph_dwd_ad_table_cnt_check", send.call_args.args[0])
        self.assertEqual(send.call_args.kwargs["mentions"], ["owner@kn.group"])
        self.assertEqual(send.call_args.kwargs["bot_id"], "bot-1")

    def test_run_never_refreshes_check_table(self):
        module = load_module()
        rows = [
            {"table_name": "dwd_ad_tt_report", "cnt": 0, "check_time": "2026-07-02 08:00:00"},
        ]

        with mock.patch.object(module, "fetch_check_rows", return_value=rows) as fetch:
            with mock.patch.object(
                module,
                "send_to_tv",
                return_value={"success": True, "status_code": 200, "response": "ok"},
            ):
                with mock.patch("builtins.print"):
                    result = module.run(
                        target_date=date(2026, 7, 1),
                        sr_password="primary-secret",
                        sr_backup_password="backup-secret",
                        skip_refresh=False,
                    )

        self.assertTrue(result["success"])
        fetch.assert_called_once()

    def test_run_skips_tv_when_no_problem_tables(self):
        module = load_module()
        rows = [
            {"table_name": table_name, "cnt": 1, "check_time": "2026-07-02 08:00:00"}
            for table_name in module.EXPECTED_TABLES
        ]

        with mock.patch.object(module, "fetch_check_rows", return_value=rows) as fetch:
            with mock.patch.object(module, "send_to_tv") as send:
                with mock.patch("builtins.print") as print_mock:
                    result = module.run(
                        target_date=date(2026, 7, 1),
                        mentions=["owner@kn.group"],
                        sr_password="primary-secret",
                        sr_backup_password="backup-secret",
                        bot_id="bot-1",
                    )

        self.assertTrue(result["success"])
        self.assertEqual(result["response"], "no_problems")
        fetch.assert_called_once()
        send.assert_not_called()
        self.assertIn("跳过TV告警发送", print_mock.call_args.args[0])

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
