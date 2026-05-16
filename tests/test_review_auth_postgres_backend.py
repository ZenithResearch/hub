from __future__ import annotations

import unittest
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


if __name__ == "__main__":
    unittest.main()
