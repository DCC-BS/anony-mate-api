"""Tests for the queue: limits are honoured, and clients are served in turn.

The suite has no async plugin, so each test drives its own event loop.
"""

import asyncio

import pytest

from anony_mate_api.services.task_store import LaneConfig, QueueFullError, TaskStore


async def _settle(times: int = 50) -> None:
    """Yield often enough for the workers to pick up whatever is waiting."""
    for _ in range(times):
        await asyncio.sleep(0)


def test_runs_at_most_the_configured_number_at_once() -> None:
    async def scenario() -> int:
        store = TaskStore(lanes={"convert": LaneConfig(workers=2, max_queued=10)})
        store.start()
        running = 0
        peak = 0
        release = asyncio.Event()

        async def work(_task: object) -> str:
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await release.wait()
            running -= 1
            return "done"

        for _ in range(6):
            _ = store.submit(work, lane="convert")

        await _settle()
        release.set()
        await _settle()
        await store.stop()
        return peak

    assert asyncio.run(scenario()) == 2, "a third job started while two were already running"


def test_refuses_submissions_once_the_backlog_is_full() -> None:
    async def scenario() -> None:
        # Deliberately not started: nothing drains, so the backlog fills up.
        store = TaskStore(lanes={"convert": LaneConfig(workers=1, max_queued=2)})

        async def work(_task: object) -> None:
            await asyncio.sleep(0)

        _ = store.submit(work, lane="convert")
        _ = store.submit(work, lane="convert")

        with pytest.raises(QueueFullError):
            _ = store.submit(work, lane="convert")

    asyncio.run(scenario())


def test_serves_clients_in_turn_rather_than_in_arrival_order() -> None:
    """One client's backlog must not park another client behind it."""

    async def scenario() -> list[str]:
        store = TaskStore(lanes={"convert": LaneConfig(workers=1, max_queued=10)})
        order: list[str] = []

        def work_for(name: str):
            async def work(_task: object) -> None:
                order.append(name)

            return work

        # "busy" queues three before "latecomer" queues its single document.
        for _ in range(3):
            _ = store.submit(work_for("busy"), lane="convert", client="busy")
        _ = store.submit(work_for("latecomer"), lane="convert", client="latecomer")

        store.start()
        await _settle(200)
        await store.stop()
        return order

    order = asyncio.run(scenario())
    assert order[:2] == ["busy", "latecomer"], f"strict arrival order would starve the latecomer: {order}"
    assert sorted(order) == ["busy", "busy", "busy", "latecomer"]


def test_reports_how_many_are_ahead_while_waiting() -> None:
    async def scenario() -> None:
        store = TaskStore(lanes={"convert": LaneConfig(workers=1, max_queued=10)})

        async def work(_task: object) -> None:
            await asyncio.sleep(0)

        first = store.submit(work, lane="convert", client="a")
        second = store.submit(work, lane="convert", client="a")

        assert first.queue_position == 1
        assert second.queue_position == 2
        assert store.queue_size("convert") == 2

    asyncio.run(scenario())


def test_drops_queued_work_whose_caller_stopped_polling() -> None:
    """Docling cannot recall a started job, so the drop has to happen first."""

    async def scenario() -> tuple[str, list[str]]:
        store = TaskStore(
            lanes={"convert": LaneConfig(workers=1, max_queued=10)},
            abandoned_after_seconds=30.0,
        )
        ran: list[str] = []

        def work_for(name: str):
            async def work(_task: object) -> None:
                ran.append(name)

            return work

        abandoned = store.submit(work_for("abandoned"), lane="convert", client="gone")
        _ = store.submit(work_for("wanted"), lane="convert", client="here")
        # Age one caller past the window instead of sleeping through it.
        abandoned.polled_at -= 60.0

        store.start()
        await _settle(200)
        await store.stop()
        return abandoned.status, ran

    status, ran = asyncio.run(scenario())
    assert status == "failed"
    assert ran == ["wanted"], f"the abandoned document should never have run: {ran}"


def test_keeps_queued_work_that_is_still_being_polled() -> None:
    async def scenario() -> list[str]:
        store = TaskStore(
            lanes={"convert": LaneConfig(workers=1, max_queued=10)},
            abandoned_after_seconds=30.0,
        )
        ran: list[str] = []

        async def work(_task: object) -> None:
            ran.append("done")

        _ = store.submit(work, lane="convert", client="here")
        store.start()
        await _settle(200)
        await store.stop()
        return ran

    assert asyncio.run(scenario()) == ["done"]


def test_a_failing_job_does_not_cost_the_lane_a_worker() -> None:
    """A worker that died on one bad job would silently shrink the lane."""

    async def scenario() -> list[str]:
        store = TaskStore(lanes={"convert": LaneConfig(workers=1, max_queued=10)})
        ran: list[str] = []

        async def explode(_task: object) -> None:
            raise RuntimeError("boom")

        async def afterwards(_task: object) -> None:
            ran.append("afterwards")

        _ = store.submit(explode, lane="convert", client="a")
        _ = store.submit(afterwards, lane="convert", client="a")

        store.start()
        await _settle(200)
        await store.stop()
        return ran

    assert asyncio.run(scenario()) == ["afterwards"]
