import importlib.util
from pathlib import Path
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "alert" / "run_alert.py"


def load_module():
    spec = importlib.util.spec_from_file_location("run_alert", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RunAlertTests(unittest.TestCase):
    def test_main_dispatches_to_selected_alert_with_shared_arguments(self):
        module = load_module()
        fake_alert = mock.Mock()
        fake_alert.run.return_value = {"success": True, "status_code": None, "response": "ok"}

        with mock.patch.dict(module.ALERT_MODULES, {"id_marketing_dwd_cnt": fake_alert}):
            exit_code = module.main(
                [
                    "--alert",
                    "id_marketing_dwd_cnt",
                    "--dry-run",
                    "--sr-password",
                    "primary",
                    "--sr-backup-password",
                    "backup",
                    "--bot-id",
                    "bot-1",
                    "--mentions",
                    "owner@kn.group,backup@kn.group",
                    "--target-date",
                    "2026-07-01",
                ]
            )

        self.assertEqual(exit_code, 0)
        fake_alert.run.assert_called_once()
        kwargs = fake_alert.run.call_args.kwargs
        self.assertTrue(kwargs["dry_run"])
        self.assertEqual(kwargs["sr_password"], "primary")
        self.assertEqual(kwargs["sr_backup_password"], "backup")
        self.assertEqual(kwargs["bot_id"], "bot-1")
        self.assertEqual(kwargs["mentions"], ["owner@kn.group", "backup@kn.group"])
        self.assertEqual(str(kwargs["target_date"]), "2026-07-01")
        self.assertTrue(kwargs["skip_refresh"])

    def test_main_passes_capital_only_to_mx_ltv_alert(self):
        module = load_module()
        fake_alert = mock.Mock()
        fake_alert.run.return_value = {"success": True, "status_code": None, "response": "ok"}

        with mock.patch.dict(module.ALERT_MODULES, {"mx_capital_ltv": fake_alert}):
            exit_code = module.main(["--alert", "mx_capital_ltv", "--capital", "new_share", "--dry-run"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(fake_alert.run.call_args.kwargs["capital"], "new_share")

    def test_main_passes_marketing_dwd_country_arguments(self):
        module = load_module()
        fake_alert = mock.Mock()
        fake_alert.run.return_value = {"success": True, "status_code": None, "response": "ok"}

        with mock.patch.dict(module.ALERT_MODULES, {"marketing_dwd_cnt": fake_alert}):
            exit_code = module.main(
                [
                    "--alert",
                    "marketing_dwd_cnt",
                    "--profile",
                    "ph",
                    "--country-name",
                    "菲律宾",
                    "--platform-type",
                    "投放",
                    "--check-table",
                    "testdb.ph_dwd_ad_table_cnt_check",
                    "--table-names",
                    "dwd_ad_tt_report,dwd_ad_tt_campaign_get",
                    "--skip-refresh",
                    "--dry-run",
                ]
            )

        self.assertEqual(exit_code, 0)
        kwargs = fake_alert.run.call_args.kwargs
        self.assertEqual(kwargs["profile"], "ph")
        self.assertEqual(kwargs["country_name"], "菲律宾")
        self.assertEqual(kwargs["platform_type"], "投放")
        self.assertEqual(kwargs["check_table"], "testdb.ph_dwd_ad_table_cnt_check")
        self.assertEqual(kwargs["table_names"], "dwd_ad_tt_report,dwd_ad_tt_campaign_get")
        self.assertTrue(kwargs["skip_refresh"])


if __name__ == "__main__":
    unittest.main()
