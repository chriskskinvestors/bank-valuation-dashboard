"""data.db.database_url — the Postgres URL is assembled from Cloud SQL parts
with the password from a secret mount (DB_PASSWORD), never shipped as a plain
DATABASE_URL env var (pre-assessment security sweep, 2026-09-16)."""
import unittest

from data.db import database_url

_PARTS = {"DB_USER": "dashboard", "DB_PASSWORD": "s3cr3t",
          "DB_NAME": "dashboard",
          "INSTANCE_CONNECTION_NAME": "proj:us-central1:bank-dashboard-db"}


class TestDatabaseUrl(unittest.TestCase):
    def test_parts_assemble_the_unix_socket_url(self):
        self.assertEqual(
            database_url(_PARTS),
            "postgresql+psycopg2://dashboard:s3cr3t@/dashboard"
            "?host=/cloudsql/proj:us-central1:bank-dashboard-db")

    def test_password_special_characters_are_url_quoted(self):
        url = database_url({**_PARTS, "DB_PASSWORD": "p@ss:w/rd#1"})
        self.assertIn(":p%40ss%3Aw%2Frd%231@/", url)

    def test_explicit_database_url_wins(self):
        self.assertEqual(
            database_url({**_PARTS, "DATABASE_URL": "postgresql://x@/y"}),
            "postgresql://x@/y")

    def test_partial_parts_are_absent_never_half_built(self):
        for missing in _PARTS:
            env = {k: v for k, v in _PARTS.items() if k != missing}
            with self.subTest(missing=missing):
                self.assertEqual(database_url(env), "")

    def test_no_config_means_sqlite(self):
        self.assertEqual(database_url({}), "")
        self.assertEqual(database_url({**_PARTS, "DB_PASSWORD": "  "}), "")


if __name__ == "__main__":
    unittest.main()
