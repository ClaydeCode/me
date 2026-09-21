import asyncio

import pytest

from clayde.service.queue import JobQueue, Job, QueueFullError


@pytest.mark.asyncio
async def test_enqueue_and_dequeue():
    q = JobQueue(maxsize=2)
    job = Job(id="abc", text="hi", timestamp=1)
    q.enqueue(job)
    got = await q.get()
    assert got == job


@pytest.mark.asyncio
async def test_enqueue_raises_when_full():
    q = JobQueue(maxsize=1)
    q.enqueue(Job(id="a", text="", timestamp=0))
    with pytest.raises(QueueFullError):
        q.enqueue(Job(id="b", text="", timestamp=0))


@pytest.mark.asyncio
async def test_get_blocks_until_enqueued():
    q = JobQueue(maxsize=2)
    job = Job(id="abc", text="hi", timestamp=1)

    async def producer():
        await asyncio.sleep(0.01)
        q.enqueue(job)

    asyncio.create_task(producer())
    got = await asyncio.wait_for(q.get(), timeout=1.0)
    assert got == job


def test_job_origin_defaults_to_pebble():
    job = Job(id="1", text="hi", timestamp=0)
    assert job.origin == "pebble"


def test_job_origin_can_be_scheduler():
    job = Job(id="1", text="hi", timestamp=0, origin="scheduler")
    assert job.origin == "scheduler"
