from __future__ import annotations

import asyncio
import unittest

from services.eventbus.broker import Event, EventBroker


class EventBusBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_all_unblocks_subscribers_cleanly(self) -> None:
        broker = EventBroker()
        stream = broker.subscribe("queue.job.*")

        first_task = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        await broker.publish(Event(topic="queue.job.enqueued", source="test", payload={"queue_name": "workers"}))
        first = await first_task
        self.assertEqual(first.topic, "queue.job.enqueued")

        pending_next = asyncio.create_task(anext(stream, None))
        await asyncio.sleep(0)
        await broker.close_all()
        self.assertIsNone(await pending_next)

    async def test_published_event_payload_round_trips(self) -> None:
        broker = EventBroker()
        stream = broker.subscribe("queue.job.*")

        event_task = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        publish_task = asyncio.create_task(
            broker.publish(Event(topic="queue.job.enqueued", source="test", payload={"queue_name": "workers"}))
        )
        event = await event_task
        await publish_task

        self.assertEqual(event.to_payload()["payload"]["queue_name"], "workers")
        await broker.close_all()
        await anext(stream, None)


if __name__ == "__main__":
    unittest.main()
