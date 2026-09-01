"""Multi-client load harness for the AnonyMate task pipeline.

Mirrors what the browser actually does: every simulated client owns a queue of
documents and works through it **one at a time** (see ``useDocumentQueue.pump``
in the frontend), submitting a task, polling ``/task/{id}`` at the frontend's
interval, then collecting the resource. Running several clients at once is
therefore the honest reproduction of several browser tabs, which is where the
pipeline's concurrency shows up.

Each phase is timed separately so the report can say *where* the time goes:

* ``submit``  - how long the API took to accept the work and hand back a task id
* ``queue``   - accepted, but the downstream service has not started it yet
* ``work``    - downstream is actively running (Docling OCR / GLiNER scan)
* ``collect`` - fetching the finished resource

A stage that grows with the client count is a bottleneck; one that stays flat
is not. ``queue`` growing while ``work`` stays flat means requests are waiting
for a busy worker rather than the work itself getting slower.

Usage:
    uv run python tools/loadtest.py --clients 4 --docs 3
    uv run python tools/loadtest.py --clients 4 --files test-data/long/*.txt
    uv run python tools/loadtest.py --clients 2 --files doc.pdf --csv runs.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import shutil
import statistics
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

#: Extensions that go through Docling first; anything else is submitted as text.
BINARY_SUFFIXES = {".pdf", ".docx", ".pptx", ".xlsx", ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".gif", ".webp"}

#: Same labels the frontend's default profile sends, so scan cost is realistic.
DEFAULT_ENTITY_TYPES = {
    "person": "A person, can be first name, last name or lastname and firstname",
    "location": "A location",
}

Stage = Literal["convert", "redact"]


@dataclass
class Attempt:
    """One task from submission to collection, as one client experienced it."""

    client: int
    document: str
    stage: Stage
    started_at: float
    submit_s: float = 0.0
    queue_s: float = 0.0
    work_s: float = 0.0
    collect_s: float = 0.0
    polls: int = 0
    chars: int = 0
    #: Highest queue position the API ever reported, None if it never did.
    max_queue_position: int | None = None
    error: str | None = None

    @property
    def total_s(self) -> float:
        return self.submit_s + self.queue_s + self.work_s + self.collect_s


@dataclass
class Recorder:
    """Collects attempts and the live task census used for the timeline."""

    attempts: list[Attempt] = field(default_factory=list)
    #: (seconds since start, stage, how many tasks were in flight) samples.
    census: list[tuple[float, str, int]] = field(default_factory=list)
    inflight: Counter[str] = field(default_factory=Counter)


async def _poll_until_done(
    client: httpx.AsyncClient,
    task_id: str,
    attempt: Attempt,
    poll_interval: float,
    timeout: float,
) -> str:
    """Poll one task to completion, splitting the wait into queue vs work.

    Returns:
        The resource id of the finished task.

    Raises:
        RuntimeError: If the task failed or outlived ``timeout``.
    """
    deadline = time.monotonic() + timeout
    last_seen_at = time.monotonic()
    # The store marks a task "running" the moment it picks the work up, before
    # the downstream service has accepted it, so a task can flip back to
    # "pending" once Docling reports its real queue state. Attributing each
    # interval to the status observed at its start survives that flip, where
    # a single first-seen-running timestamp would silently count queue as work.
    last_status = "pending"

    while True:
        response = await client.get(f"/task/{task_id}")
        attempt.polls += 1

        if response.status_code == 404:
            # The store dropped it: a replica restart, or the TTL expired.
            raise RuntimeError(f"task {task_id} vanished from the store")
        response.raise_for_status()
        state = response.json()

        now = time.monotonic()
        elapsed = now - last_seen_at
        if last_status == "running":
            attempt.work_s += elapsed
        else:
            attempt.queue_s += elapsed
        last_seen_at = now
        last_status = state["status"]

        position = state.get("queue_position")
        if position is not None:
            attempt.max_queue_position = max(attempt.max_queue_position or 0, position)

        if state["status"] == "failed":
            raise RuntimeError(state.get("error") or "task failed")

        if state["status"] == "finished":
            return state["resource_id"]

        if now > deadline:
            raise RuntimeError(f"task {task_id} still {state['status']} after {timeout:.0f}s")

        await asyncio.sleep(poll_interval)


async def _run_task(
    client: httpx.AsyncClient,
    recorder: Recorder,
    attempt: Attempt,
    submit: Any,
    poll_interval: float,
    timeout: float,
) -> Any:
    """Submit, poll and collect one task, timing every phase."""
    recorder.inflight[attempt.stage] += 1
    try:
        submitted_at = time.monotonic()
        response = await submit()
        response.raise_for_status()
        attempt.submit_s = time.monotonic() - submitted_at
        task_id = response.json()["task_id"]

        resource_id = await _poll_until_done(client, task_id, attempt, poll_interval, timeout)

        collect_at = time.monotonic()
        resource = await client.get(f"/resource/{resource_id}")
        resource.raise_for_status()
        attempt.collect_s = time.monotonic() - collect_at
        return resource.json()
    finally:
        recorder.inflight[attempt.stage] -= 1
        recorder.attempts.append(attempt)


async def _process_document(
    client: httpx.AsyncClient,
    recorder: Recorder,
    client_id: int,
    path: Path,
    args: argparse.Namespace,
    origin: float,
) -> None:
    """Convert (when needed) and then redact one document, as the browser does."""
    text: str

    if path.suffix.lower() in BINARY_SUFFIXES:
        attempt = Attempt(client_id, path.name, "convert", time.monotonic() - origin)
        content = path.read_bytes()

        async def submit_convert() -> httpx.Response:
            return await client.post(
                "/convert/doc/async",
                files={"file": (path.name, content, "application/octet-stream")},
            )

        try:
            result = await _run_task(client, recorder, attempt, submit_convert, args.poll_interval, args.timeout)
        except Exception as error:
            attempt.error = f"{type(error).__name__}: {error}"
            return
        text = result["text"]
        attempt.chars = len(text)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")

    attempt = Attempt(client_id, path.name, "redact", time.monotonic() - origin, chars=len(text))
    payload = {
        "text": text,
        "entity_types": DEFAULT_ENTITY_TYPES,
        "threshold": args.threshold,
        "blacklist": [],
    }

    async def submit_redact() -> httpx.Response:
        return await client.post("/redact/async", json=payload)

    try:
        _ = await _run_task(client, recorder, attempt, submit_redact, args.poll_interval, args.timeout)
    except Exception as error:
        attempt.error = f"{type(error).__name__}: {error}"


async def _client_worker(
    recorder: Recorder,
    client_id: int,
    documents: list[Path],
    args: argparse.Namespace,
    origin: float,
) -> None:
    """One simulated browser tab: its documents, strictly one after another."""
    limits = httpx.Limits(max_connections=8)
    # The API queues per client and serves clients in turn, so each simulated
    # tab must identify itself or the whole run looks like one busy browser.
    headers = {"X-Client-Id": f"loadtest-{client_id}"}
    async with httpx.AsyncClient(
        base_url=args.api_url, timeout=args.http_timeout, limits=limits, headers=headers
    ) as client:
        for path in documents:
            await _process_document(client, recorder, client_id, path, args, origin)


async def _sample_census(recorder: Recorder, origin: float, stop: asyncio.Event) -> None:
    """Record how many tasks sit in each stage, once a second."""
    while not stop.is_set():
        elapsed = time.monotonic() - origin
        for stage in ("convert", "redact"):
            recorder.census.append((elapsed, stage, recorder.inflight[stage]))
        try:
            await asyncio.wait_for(stop.wait(), timeout=1.0)
        except TimeoutError:
            continue


async def _sample_gpu(samples: list[tuple[float, int, float, float]], origin: float, stop: asyncio.Event) -> None:
    """Record utilisation and memory for *every* GPU while the run is in flight.

    Sampling only the first card is a trap on a multi-GPU box: the model may
    well be pinned to another one, and card 0 then reports the desktop's idle
    load as if it were the service's.
    """
    query = "--query-gpu=index,utilization.gpu,memory.used"
    while not stop.is_set():
        process = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            query,
            "--format=csv,noheader,nounits",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        stdout, _ = await process.communicate()
        elapsed = time.monotonic() - origin
        for line in stdout.decode().strip().splitlines():
            index, util, memory = (part.strip() for part in line.split(","))
            samples.append((elapsed, int(index), float(util), float(memory)))
        try:
            await asyncio.wait_for(stop.wait(), timeout=1.0)
        except TimeoutError:
            continue


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))
    return ordered[index]


def _format_stage_table(attempts: list[Attempt]) -> str:
    header = f"{'stage':9} {'n':>3} {'submit':>8} {'queue p50':>10} {'queue max':>10} {'work p50':>9} {'work max':>9} {'total p50':>10}"
    lines = [header, "-" * len(header)]

    for stage in ("convert", "redact"):
        done = [attempt for attempt in attempts if attempt.stage == stage and attempt.error is None]
        if not done:
            continue
        lines.append(
            f"{stage:9} {len(done):>3}"
            f" {statistics.mean(a.submit_s for a in done):>7.2f}s"
            f" {_percentile([a.queue_s for a in done], 0.5):>9.1f}s"
            f" {max(a.queue_s for a in done):>9.1f}s"
            f" {_percentile([a.work_s for a in done], 0.5):>8.1f}s"
            f" {max(a.work_s for a in done):>8.1f}s"
            f" {_percentile([a.total_s for a in done], 0.5):>9.1f}s"
        )
    return "\n".join(lines)


def _format_timeline(census: list[tuple[float, str, int]]) -> str:
    """Render in-flight counts per second, so pile-ups are visible."""
    if not census:
        return "(no samples)"

    blocks = " ▁▂▃▄▅▆▇█"
    lines = []
    for stage in ("convert", "redact"):
        series = [count for _, sampled_stage, count in census if sampled_stage == stage]
        if not series or max(series) == 0:
            continue
        peak = max(series)
        bar = "".join(blocks[min(len(blocks) - 1, round(count / peak * (len(blocks) - 1)))] for count in series)
        lines.append(f"{stage:9} peak={peak:<3} {bar}")
    return "\n".join(lines) if lines else "(nothing in flight)"


def _print_per_client(args: argparse.Namespace, attempts: list[Attempt]) -> None:
    """Show when each client finished, which is where fair ordering shows up."""
    if len(set(args.per_client)) <= 1:
        return

    print()
    print("per client (asymmetric run — is the small client served in turn?):")
    print(f"  {'client':8} {'docs':>5} {'first done':>11} {'all done':>10}")
    for client_id, count in enumerate(args.per_client):
        mine = [a for a in attempts if a.client == client_id and a.stage == "redact" and not a.error]
        if not mine:
            print(f"  {client_id:<8} {count:>5} {'-':>11} {'-':>10}")
            continue
        ends = sorted(a.started_at + a.total_s for a in mine)
        print(f"  {client_id:<8} {count:>5} {ends[0]:>10.1f}s {ends[-1]:>9.1f}s")


def _report(
    args: argparse.Namespace, recorder: Recorder, gpu: list[tuple[float, int, float, float]], wall_s: float
) -> None:
    attempts = recorder.attempts
    failed = [attempt for attempt in attempts if attempt.error]
    done = [attempt for attempt in attempts if not attempt.error]

    print()
    print("=" * 78)
    print(f"clients={args.clients}  documents/client={len(args.resolved_documents)}  poll={args.poll_interval}s")
    print(f"wall clock: {wall_s:.1f}s   completed tasks: {len(done)}   failed: {len(failed)}")
    if done:
        print(f"throughput: {len(done) / wall_s * 60:.1f} tasks/min")
    print("=" * 78)
    print()
    print(_format_stage_table(attempts))
    positioned = [a for a in attempts if a.max_queue_position is not None]
    print()
    if positioned:
        deepest = max(a.max_queue_position or 0 for a in positioned)
        print(f"queue position reported for {len(positioned)}/{len(attempts)} tasks, deepest seen: {deepest}")
    else:
        print("queue position: never reported (downstream stayed idle, or it is not being forwarded)")

    _print_per_client(args, attempts)

    print()
    print("in-flight over time (1 sample/s):")
    print(_format_timeline(recorder.census))

    if gpu:
        print()
        for index in sorted({sample[1] for sample in gpu}):
            utils = [util for _, gpu_index, util, _ in gpu if gpu_index == index]
            memory = [used for _, gpu_index, _, used in gpu if gpu_index == index]
            print(
                f"gpu{index}: util mean={statistics.mean(utils):>3.0f}% max={max(utils):>3.0f}%"
                f"   mem max={max(memory):>6.0f} MiB"
            )

    if failed:
        print()
        print("failures:")
        for attempt in failed:
            print(f"  client {attempt.client} {attempt.stage:8} {attempt.document:32} {attempt.error}")

    rejected = [attempt for attempt in attempts if attempt.error and "429" in attempt.error]
    if rejected:
        print()
        print(f"refused with 429 (queue full): {len(rejected)} — backpressure worked, the caller can retry")

    print()
    print("reading it:")
    print("  queue grows with clients, work flat -> downstream is saturated, requests are waiting")
    print("  work grows with clients             -> the model is thrashing (GPU/CPU contention)")
    print("  both flat                           -> no bottleneck at this client count")


def _write_csv(path: Path, attempts: list[Attempt]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "client",
            "document",
            "stage",
            "started_at",
            "submit_s",
            "queue_s",
            "work_s",
            "collect_s",
            "total_s",
            "polls",
            "chars",
            "max_queue_position",
            "error",
        ])
        for attempt in attempts:
            writer.writerow([
                attempt.client,
                attempt.document,
                attempt.stage,
                f"{attempt.started_at:.2f}",
                f"{attempt.submit_s:.3f}",
                f"{attempt.queue_s:.3f}",
                f"{attempt.work_s:.3f}",
                f"{attempt.collect_s:.3f}",
                f"{attempt.total_s:.3f}",
                attempt.polls,
                attempt.chars,
                attempt.max_queue_position if attempt.max_queue_position is not None else "",
                attempt.error or "",
            ])


def _resolve_documents(args: argparse.Namespace) -> list[Path]:
    if args.files:
        paths = [Path(pattern) for pattern in args.files]
        missing = [path for path in paths if not path.is_file()]
        if missing:
            raise SystemExit(f"not a file: {', '.join(str(path) for path in missing)}")
        return paths

    corpus = sorted(Path(args.corpus).glob("*.txt")) if args.corpus else []
    if not corpus:
        raise SystemExit("no documents: pass --files, or --corpus pointing at a directory of .txt files")
    return corpus[: args.docs]


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-url", default="http://localhost:8000", help="AnonyMate API base URL")
    parser.add_argument("--clients", type=int, default=3, help="Simulated browser tabs, running in parallel")
    parser.add_argument("--docs", type=int, default=2, help="Documents per client when taking them from --corpus")
    parser.add_argument(
        "--client-docs",
        help=(
            "Documents per client as a comma-separated list, e.g. '6,1,1': one busy client "
            "and two with a single document. Sets the client count, and shows whether a "
            "latecomer is served in turn or parked behind the busy one."
        ),
    )
    parser.add_argument("--files", nargs="*", help="Explicit documents; each client processes all of them in order")
    parser.add_argument(
        "--corpus",
        default="../gliner/gliner-test/test-data/long",
        help="Directory of .txt documents to draw from when --files is not given",
    )
    parser.add_argument(
        "--poll-interval", type=float, default=3.0, help="Seconds between task polls, as the frontend does"
    )
    parser.add_argument("--threshold", type=float, default=0.8, help="Detection confidence threshold")
    parser.add_argument("--timeout", type=float, default=1800.0, help="Seconds before a single task is given up on")
    parser.add_argument("--http-timeout", type=float, default=300.0, help="Per-request HTTP timeout")
    parser.add_argument("--csv", type=Path, help="Write the raw per-task rows here")
    parser.add_argument("--no-gpu", action="store_true", help="Skip nvidia-smi sampling")
    args = parser.parse_args()

    args.resolved_documents = _resolve_documents(args)

    if args.client_docs:
        try:
            args.per_client = [int(part) for part in args.client_docs.split(",")]
        except ValueError:
            raise SystemExit("--client-docs takes a comma-separated list of integers") from None
        args.clients = len(args.per_client)
    else:
        args.per_client = [len(args.resolved_documents)] * args.clients

    print(f"api        : {args.api_url}")
    print(f"clients    : {args.clients}")
    print(f"documents  : {', '.join(path.name for path in args.resolved_documents)}")
    print(f"total tasks: up to {args.clients * len(args.resolved_documents) * 2} (convert + redact)")
    print("running ...")

    recorder = Recorder()
    gpu_samples: list[tuple[float, int, float, float]] = []
    stop = asyncio.Event()
    origin = time.monotonic()

    samplers = [asyncio.create_task(_sample_census(recorder, origin, stop))]
    if not args.no_gpu and shutil.which("nvidia-smi"):
        samplers.append(asyncio.create_task(_sample_gpu(gpu_samples, origin, stop)))

    workers = [
        _client_worker(
            recorder,
            client_id,
            # Repeat the corpus to reach this client's document count, so an
            # asymmetric run needs no extra files on disk.
            [args.resolved_documents[index % len(args.resolved_documents)] for index in range(count)],
            args,
            origin,
        )
        for client_id, count in enumerate(args.per_client)
    ]
    await asyncio.gather(*workers)

    stop.set()
    await asyncio.gather(*samplers)
    wall_s = time.monotonic() - origin

    _report(args, recorder, gpu_samples, wall_s)

    if args.csv:
        _write_csv(args.csv, recorder.attempts)
        print(f"\nraw rows written to {args.csv}")

    return 1 if any(attempt.error for attempt in recorder.attempts) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
