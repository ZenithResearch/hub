from __future__ import annotations

import tempfile
import unittest

from fastapi.testclient import TestClient

from inbox.http import create_app
from inbox.store import QueueStore


class QueueHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.store = QueueStore(db_path=f"{self.tmpdir.name}/queue.db")
        self.client_context = TestClient(create_app(self.store))
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.tmpdir.cleanup()

    def test_enqueue_returns_legacy_and_canonical_ids_and_preserves_process_path(self) -> None:
        response = self.client.post(
            "/queues/workspace/enqueue",
            json={
                "event_type": "review_submitted",
                "process_path": "process-queued-review",
                "sender": "tester",
                "message_body": "review-123",
                "payload": {"review_id": "review-123"},
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["id"], body["message_id"])

        claimed = self.client.post("/queues/workspace/dequeue", params={"worker_id": "frank"})
        self.assertEqual(claimed.status_code, 200)
        message = claimed.json()["message"]
        self.assertEqual(message["process_path"], "process-queued-review")
        self.assertEqual(message["event_type"], "review_submitted")


if __name__ == "__main__":
    unittest.main()
