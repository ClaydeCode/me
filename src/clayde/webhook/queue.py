"""In-memory job queue for the Pebble webhook."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


class QueueFullError(Exception):
    """Raised when the queue is at capacity."""


@dataclass(frozen=True)
class PebbleJob:
    id: str
    text: str
    timestamp: int


class JobQueue:
    """Thin wrapper over ``asyncio.Queue[PebbleJob]`` with non-blocking enqueue."""

    def __init__(self, maxsize: int):
        self._q: asyncio.Queue[PebbleJob] = asyncio.Queue(maxsize=maxsize)

    def enqueue(self, job: PebbleJob) -> None:
        """Non-blocking enqueue. Raises ``QueueFullError`` when full."""
        try:
            self._q.put_nowait(job)
        except asyncio.QueueFull as e:
            raise QueueFullError() from e

    async def get(self) -> PebbleJob:
        return await self._q.get()
