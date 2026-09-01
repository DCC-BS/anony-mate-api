"""In-process store for long-running jobs, addressed by task id.

Proxies and WAFs cut long-held connections, so callers submit work, get a task
id back immediately, poll a cheap status endpoint, then fetch the result once.
The result is dropped as it is read, and abandoned entries expire, so the store
does not grow.

Work does not start on submission: it joins a lane, and a fixed pool of workers
drains that lane. The downstream services decide how much can actually run at
once — Docling has two worker slots, and neither it nor GLiNER can be changed
from here — so submitting everything immediately only moves the backlog into a
queue we cannot see, position, prioritise or cancel. Holding it here instead
keeps all four possible.

Lanes drain round-robin over the clients waiting in them rather than in plain
arrival order, so one client queueing twenty documents cannot park everyone
else behind it.

State lives in the worker process, so a caller has to keep polling the instance
that accepted the submission. That is the same contract docling's async API
has; behind a load balancer it needs a single replica or sticky sessions.
"""

import asyncio
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from dcc_backend_common.logger import get_logger

logger = get_logger("task_store")

TaskStatus = Literal["pending", "running", "finished", "failed"]

#: How long a finished result is kept for collection before it is discarded.
DEFAULT_TTL_SECONDS = 3600.0

#: Lane used when a caller does not name one.
DEFAULT_LANE = "default"

#: A queued job whose caller has not polled for this long is dropped unrun.
DEFAULT_ABANDONED_AFTER_SECONDS = 60.0


class QueueFullError(Exception):
    """Raised when a lane's backlog is full and the work cannot be accepted."""

    def __init__(self, lane: str, max_queued: int) -> None:
        super().__init__(f"queue for {lane!r} is full ({max_queued} waiting)")
        self.lane = lane
        self.max_queued = max_queued


@dataclass(frozen=True)
class LaneConfig:
    """How much of one kind of work may run, and how much may wait."""

    #: Jobs of this kind running at once. Match the downstream service.
    workers: int
    #: Jobs allowed to wait before submissions are refused.
    max_queued: int


@dataclass
class TaskData:
    """One submitted job and, once it finishes, where its result waits."""

    id: str
    lane: str = DEFAULT_LANE
    status: TaskStatus = "pending"
    #: Fraction done in [0, 1], or None while the job cannot report it.
    progress: float | None = None
    #: How many jobs are ahead of this one, while it is still waiting.
    queue_position: int | None = None
    resource_id: str | None = None
    error: str | None = None
    updated_at: float = field(default_factory=time.monotonic)
    #: When the caller last asked about this task. Starts at submission, so a
    #: task that is never polled at all still ages out of the queue.
    polled_at: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        self.updated_at = time.monotonic()

    def mark_polled(self) -> None:
        self.polled_at = time.monotonic()


@dataclass
class _Waiting:
    """A job that has been accepted but has not been given a worker yet."""

    task: TaskData
    work: Callable[[TaskData], Awaitable[Any]]


class _Lane:
    """One lane's backlog, kept per client so it can be drained fairly."""

    def __init__(self, name: str, config: LaneConfig) -> None:
        self.name = name
        self.config = config
        # Ordered so the round-robin has a stable rotation; a client is
        # appended when it first queues and dropped when its backlog empties.
        self._by_client: OrderedDict[str, deque[_Waiting]] = OrderedDict()
        self._arrivals = asyncio.Semaphore(0)
        self.size = 0

    def append(self, client: str, entry: _Waiting) -> None:
        self._by_client.setdefault(client, deque()).append(entry)
        self.size += 1
        self._arrivals.release()

    async def take(self) -> _Waiting:
        """Wait for the next job, taking one client's turn at a time.

        Raises:
            RuntimeError: If a permit exists with no backlog behind it. That
                cannot happen while append and take are the only writers, but
                an unguarded ``StopIteration`` here would surface as an opaque
                RuntimeError inside the worker rather than as this message.
        """
        await self._arrivals.acquire()

        entries = iter(self._by_client.items())
        pair = next(entries, None)
        if pair is None:
            raise RuntimeError(f"lane {self.name!r} released a permit with an empty backlog")

        client, backlog = pair
        entry = backlog.popleft()
        self.size -= 1

        if backlog:
            # This client has more waiting: send them to the back of the
            # rotation so every other client is served before their next one.
            self._by_client.move_to_end(client)
        else:
            del self._by_client[client]

        return entry

    def drain_order(self) -> list[str]:
        """Task ids in the order this lane will actually hand them out."""
        backlogs = [(client, list(entries)) for client, entries in self._by_client.items()]
        order: list[str] = []

        while backlogs:
            still_waiting: list[tuple[str, list[_Waiting]]] = []
            for client, entries in backlogs:
                order.append(entries[0].task.id)
                if len(entries) > 1:
                    still_waiting.append((client, entries[1:]))
            backlogs = still_waiting

        return order


class TaskStore:
    """Queues coroutines, runs them under a per-lane limit, hands results out once."""

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        lanes: dict[str, LaneConfig] | None = None,
        abandoned_after_seconds: float = DEFAULT_ABANDONED_AFTER_SECONDS,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._abandoned_after_seconds = abandoned_after_seconds
        self._lane_configs = dict(lanes or {})
        self._lane_configs.setdefault(DEFAULT_LANE, LaneConfig(workers=8, max_queued=256))
        self._lanes: dict[str, _Lane] = {name: _Lane(name, config) for name, config in self._lane_configs.items()}
        self._tasks: dict[str, TaskData] = {}
        self._resources: dict[str, Any] = {}
        self._running: set[str] = set()
        self._workers: list[asyncio.Task[None]] = []

    def start(self) -> None:
        """Start every lane's workers. Call once, from the app's lifespan."""
        if self._workers:
            return

        for lane in self._lanes.values():
            for index in range(lane.config.workers):
                self._workers.append(asyncio.create_task(self._worker(lane), name=f"{lane.name}-{index}"))

        logger.info(
            "Task workers started",
            lanes={name: lane.config.workers for name, lane in self._lanes.items()},
        )

    async def stop(self) -> None:
        """Cancel the workers. In-flight jobs are cancelled with them."""
        for worker in self._workers:
            _ = worker.cancel()
        if self._workers:
            _ = await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    def submit(
        self,
        work: Callable[[TaskData], Awaitable[Any]],
        lane: str = DEFAULT_LANE,
        client: str = "anonymous",
    ) -> TaskData:
        """Queue ``work`` and return its task, already pollable.

        Args:
            work: Coroutine function receiving its own task, so it can report
                progress while it runs. Its return value becomes the resource.
            lane: Which limit this work runs under.
            client: Who submitted it, so the lane can drain clients fairly.

        Returns:
            The task, waiting or running depending on how busy the lane is.

        Raises:
            QueueFullError: If the lane's backlog is already full.
        """
        self._expire()

        target = self._lanes.get(lane) or self._lanes[DEFAULT_LANE]
        if target.size >= target.config.max_queued:
            logger.warning("Refusing submission, lane full", lane=target.name, waiting=target.size)
            raise QueueFullError(target.name, target.config.max_queued)

        task = TaskData(id=uuid.uuid4().hex, lane=target.name)
        self._tasks[task.id] = task
        target.append(client, _Waiting(task=task, work=work))
        self._refresh_positions(target)
        return task

    async def _worker(self, lane: _Lane) -> None:
        """Take one job at a time from ``lane`` and run it to completion.

        Only cancellation ends this loop. Anything else is logged and the slot
        is reused: a worker that died on one bad job would leave the lane
        quietly running short for the rest of the process's life.
        """
        while True:
            try:
                entry = await lane.take()
                self._refresh_positions(lane)

                if self._is_abandoned(entry.task):
                    self._discard(entry.task)
                    continue

                await self._run(entry.task, entry.work)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Queue worker recovered from an unexpected error", lane=lane.name)

    def _is_abandoned(self, task: TaskData) -> bool:
        """Whether nobody has asked about this task for long enough to drop it.

        Work is dispatched to services that cannot be recalled once started, so
        the only moment a vanished caller's job can still be dropped is just
        before it is handed to a worker slot.
        """
        return time.monotonic() - task.polled_at > self._abandoned_after_seconds

    def _discard(self, task: TaskData) -> None:
        """Drop a queued job whose caller stopped listening."""
        logger.info(
            "Dropping abandoned task before it reached a worker",
            task_id=task.id,
            lane=task.lane,
            unpolled_seconds=round(time.monotonic() - task.polled_at, 1),
        )
        task.status = "failed"
        task.error = "abandoned by the caller"
        task.queue_position = None
        task.touch()

    async def _run(self, task: TaskData, work: Callable[[TaskData], Awaitable[Any]]) -> None:
        task.status = "running"
        task.queue_position = None
        task.touch()
        self._running.add(task.id)
        try:
            result = await work(task)
        except asyncio.CancelledError:
            task.status = "failed"
            task.error = "cancelled"
            task.touch()
            raise
        except Exception as error:
            logger.exception("Task failed", task_id=task.id)
            task.status = "failed"
            task.error = str(error)
        else:
            resource_id = uuid.uuid4().hex
            self._resources[resource_id] = result
            task.resource_id = resource_id
            task.progress = 1.0
            task.status = "finished"
        finally:
            task.touch()
            self._running.discard(task.id)

    def _refresh_positions(self, lane: _Lane) -> None:
        """Restamp how many jobs are ahead of each one still waiting."""
        for position, task_id in enumerate(lane.drain_order(), start=1):
            waiting = self._tasks.get(task_id)
            if waiting is not None:
                waiting.queue_position = position

    def queue_size(self, lane: str = DEFAULT_LANE) -> int:
        """How many jobs are waiting in a lane, for health and diagnostics."""
        target = self._lanes.get(lane)
        return target.size if target else 0

    def get(self, task_id: str) -> TaskData | None:
        self._expire()
        return self._tasks.get(task_id)

    def poll(self, task_id: str) -> TaskData | None:
        """Read a task on a caller's behalf, recording that they are still there.

        Queued work is dropped when nobody asks about it, so the poll endpoint
        has to go through here rather than through ``get``.
        """
        task = self.get(task_id)
        if task is not None:
            task.mark_polled()
        return task

    def take_resource(self, resource_id: str) -> tuple[bool, Any]:
        """Hand out a result and forget it, so nothing is kept after collection.

        Returns:
            ``(True, result)`` when the resource existed, ``(False, None)``
            otherwise — the result itself may legitimately be ``None``.
        """
        self._expire()

        if resource_id not in self._resources:
            return False, None

        result = self._resources.pop(resource_id)
        for task in self._tasks.values():
            if task.resource_id == resource_id:
                del self._tasks[task.id]
                break

        return True, result

    def _expire(self) -> None:
        """Drop tasks nobody collected, and any result still attached to them.

        A task that is running, or still waiting for a worker, is never stale:
        its age says how long the queue is, not that it was abandoned.
        """
        cutoff = time.monotonic() - self._ttl_seconds
        stale = [
            task
            for task in self._tasks.values()
            if task.updated_at < cutoff and task.id not in self._running and task.status not in ("pending",)
        ]

        for task in stale:
            if task.resource_id:
                _ = self._resources.pop(task.resource_id, None)
            del self._tasks[task.id]

        if stale:
            logger.debug("Expired abandoned tasks", count=len(stale))
