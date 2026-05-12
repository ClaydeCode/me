import asyncio

import pytest

from clayde.webhook.queue import JobQueue, PebbleJob, QueueFullError


@pytest.mark.asyncio
async def test_enqueue_and_dequeue():
    q = JobQueue(maxsize=2)
    job = PebbleJob(id="abc", text="hi", timestamp=1)
    q.enqueue(job)
    got = await q.get()
    assert got == job


@pytest.mark.asyncio
async def test_enqueue_raises_when_full():
    q = JobQueue(maxsize=1)
    q.enqueue(PebbleJob(id="a", text="", timestamp=0))
    with pytest.raises(QueueFullError):
        q.enqueue(PebbleJob(id="b", text="", timestamp=0))


@pytest.mark.asyncio
async def test_get_blocks_until_enqueued():
    q = JobQueue(maxsize=2)
    job = PebbleJob(id="abc", text="hi", timestamp=1)

    async def producer():
        await asyncio.sleep(0.01)
        q.enqueue(job)

    asyncio.create_task(producer())
    got = await asyncio.wait_for(q.get(), timeout=1.0)
    assert got == job
