"""Polling a submitted job on `GET /task/{task_id}` until it leaves the queue."""

import asyncio

from httpx import AsyncClient

from anony_mate_lib.errors import raise_for_task_failure
from anony_mate_lib.models.tasks import TaskState

__all__ = ["poll_status", "raise_for_task_failure"]


async def poll_status(client: AsyncClient, task_id: str, *, interval: float = 0.5) -> TaskState:
    """Poll the task until it is `finished` or `failed` and return its final
    state.

    Polling keeps the task alive: a `pending` task that nobody asks about is
    dropped after a minute. The result itself is fetched from
    `GET /resource/{resource_id}` afterwards, exactly once.
    """
    task = await _poll_once(client, task_id)
    while task.status in ("pending", "running"):
        await asyncio.sleep(interval)
        task = await _poll_once(client, task_id)

    raise_for_task_failure(task)
    return task


async def _poll_once(client: AsyncClient, task_id: str) -> TaskState:
    response = await client.get(f"/task/{task_id}")
    response.raise_for_status()

    return TaskState.model_validate(response.json())
