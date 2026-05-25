from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.gateway_http.review_auth import ReviewAuthStore, create_review_auth_store


class ReviewAuthPostgresBackendTests(unittest.TestCase):
    def test_factory_requires_postgres_dsn(self) -> None:
        with self.assertRaisesRegex(ValueError, "Postgres review auth requires"):
            create_review_auth_store(
                postgres_dsn="",
                session_ttl_seconds=3600,
            )

    def test_factory_returns_postgres_store(self) -> None:
        with patch.object(ReviewAuthStore, "init_db", return_value=None):
            store = create_review_auth_store(
                postgres_dsn="postgresql://hub:***@db.example/hub",
                session_ttl_seconds=3600,
            )

        self.assertIsInstance(store, ReviewAuthStore)
        self.assertEqual(store.dsn, "postgresql://hub:***@db.example/hub")

    def test_postgres_connection_wrapper_translates_sqlite_placeholders(self) -> None:
        calls: list[tuple[str, tuple[object, ...]]] = []

        class FakeRawConnection:
            def execute(self, sql: str, params: tuple[object, ...] = ()) -> object:
                calls.append((sql, params))
                return object()

            def commit(self) -> None:
                return None

            def rollback(self) -> None:
                return None

            def close(self) -> None:
                return None

        wrapper = ReviewAuthStore.Connection(FakeRawConnection())
        wrapper.execute("SELECT * FROM projects WHERE id = ? AND slug = ?", ("p1", "slug"))

        self.assertEqual(
            calls,
            [("SELECT * FROM projects WHERE id = %s AND slug = %s", ("p1", "slug"))],
        )
    def test_init_db_backfills_review_access_code_rotation_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "clients.db")
            with sqlite3.connect(db_path) as conn:
                conn.executescript(
                    """
                    CREATE TABLE clients (
                        id TEXT PRIMARY KEY,
                        slug TEXT UNIQUE NOT NULL,
                        name TEXT NOT NULL,
                        rolodex_entry_path TEXT,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE projects (
                        id TEXT PRIMARY KEY,
                        client_id TEXT NOT NULL REFERENCES clients(id),
                        slug TEXT NOT NULL,
                        name TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE(client_id, slug)
                    );
                    CREATE TABLE review_deployments (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES projects(id),
                        slug TEXT NOT NULL,
                        branch TEXT NOT NULL,
                        allowed_origin TEXT NOT NULL,
                        subject_pattern TEXT,
                        active INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT,
                        UNIQUE(project_id, slug)
                    );
                    CREATE TABLE review_access_codes (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES projects(id),
                        deployment_id TEXT REFERENCES review_deployments(id),
                        label TEXT NOT NULL,
                        code_hash TEXT NOT NULL,
                        active INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL
                    );
                    """
                )

            store = ReviewAuthStore(
                backend="sqlite",
                dsn="",
                db_path=db_path,
                session_ttl_seconds=3600,
            )

            with sqlite3.connect(db_path) as conn:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(review_access_codes)")}
            self.assertIn("email", columns)
            self.assertIn("expires_at", columns)

            result = store.rotate_access_code(
                client_id="samantha-pinheiro",
                client_slug="samantha-pinheiro",
                client_name="Samantha Pinheiro",
                rolodex_entry_path=None,
                project_id="gallery",
                project_slug="gallery",
                project_name="Gallery",
                deployment_id=None,
                deployment_slug=None,
                allowed_origin=None,
                subject_pattern=None,
                access_code_id="samantha-pinheiro-gallery-review",
                access_label="Samantha Pinheiro",
                access_code="generated-test-access-code",
            )
            self.assertEqual(result["access_code_id"], "samantha-pinheiro-gallery-review")
            self.assertTrue(result["project_scoped_access"])


if __name__ == "__main__":
    unittest.main()
