import importlib
import sys
import types
import unittest
from unittest import mock


def load_module():
    fake_pymysql = types.SimpleNamespace(
        connect=mock.Mock(),
        cursors=types.SimpleNamespace(DictCursor=object),
    )
    for name in ["alert.common.sr_client", "pymysql", "pymysql.cursors"]:
        sys.modules.pop(name, None)
    with mock.patch.dict(
        sys.modules,
        {
            "pymysql": fake_pymysql,
            "pymysql.cursors": fake_pymysql.cursors,
        },
    ):
        module = importlib.import_module("alert.common.sr_client")
    return module, fake_pymysql


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


class CommonSrClientTests(unittest.TestCase):
    def test_build_config_uses_env_defaults_and_password_arguments(self):
        module, _ = load_module()

        with mock.patch.dict(
            module.os.environ,
            {
                "SR_HOST": "sr.example.com",
                "SR_PORT": "9031",
                "SR_DB": "ods",
                "SR_USERNAME": "e_load",
                "SR_BACKUP_USERNAME": "e_backup",
            },
            clear=True,
        ):
            config = module.build_config(sr_password="primary", sr_backup_password="backup")

        self.assertEqual(config.host, "sr.example.com")
        self.assertEqual(config.port, 9031)
        self.assertEqual(config.db, "ods")
        self.assertEqual(config.primary.username, "e_load")
        self.assertEqual(config.primary.password, "primary")
        self.assertEqual(config.backup.username, "e_backup")
        self.assertEqual(config.backup.password, "backup")

    def test_get_connection_falls_back_to_backup_account(self):
        module, fake_pymysql = load_module()
        config = module.StarRocksConfig(
            host="sr.example.com",
            port=9030,
            db="testdb",
            primary=module.StarRocksAccount(username="e_load", password="bad"),
            backup=module.StarRocksAccount(username="backup_user", password="good"),
        )
        fake_conn = FakeConnection()
        fake_pymysql.connect.side_effect = [RuntimeError("primary failed"), fake_conn]

        conn = module.get_connection(config)

        self.assertIs(conn, fake_conn)
        self.assertEqual(fake_pymysql.connect.call_count, 2)
        self.assertEqual(fake_pymysql.connect.call_args.kwargs["user"], "backup_user")

    def test_query_all_executes_sql_with_params_and_closes_connection(self):
        module, fake_pymysql = load_module()
        fake_conn = FakeConnection(rows=[{"cnt": 3}])
        fake_pymysql.connect.return_value = fake_conn
        config = module.StarRocksConfig(
            host="sr.example.com",
            port=9030,
            db="testdb",
            primary=module.StarRocksAccount(username="e_load", password="secret"),
            backup=module.StarRocksAccount(username="backup_user", password="backup"),
        )

        rows = module.query_all("select * from t where dt = %s", params=("2026-07-01",), config=config)

        self.assertEqual(rows, [{"cnt": 3}])
        self.assertEqual(fake_conn.cursor_obj.executed, [("select * from t where dt = %s", ("2026-07-01",))])
        self.assertTrue(fake_conn.closed)

    def test_execute_statements_runs_multiple_sqls_on_one_connection(self):
        module, fake_pymysql = load_module()
        fake_conn = FakeConnection()
        fake_pymysql.connect.return_value = fake_conn
        config = module.StarRocksConfig(
            host="sr.example.com",
            port=9030,
            db="testdb",
            primary=module.StarRocksAccount(username="e_load", password="secret"),
            backup=module.StarRocksAccount(username="backup_user", password="backup"),
        )

        module.execute_statements(["create table t", "insert into t select 1"], config=config)

        self.assertEqual(fake_conn.cursor_obj.executed, [("create table t", None), ("insert into t select 1", None)])
        self.assertTrue(fake_conn.closed)


if __name__ == "__main__":
    unittest.main()
