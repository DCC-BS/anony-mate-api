import asyncio
import json
from typing import Literal

import httpx
from pydantic import BaseModel

API_BASE_URL = "http://localhost:8000"


class OptionsError(Exception):
    """The submission was rejected or answered without a task id."""


class TaskState(BaseModel):
    """Where a submitted job stands, cheap enough to poll in a loop."""

    task_id: str
    status: Literal["pending", "running", "finished", "failed"]
    progress: float | None = None  # Fraction of the work done, in [0, 1]; null while unknown
    queue_position: int | None = None  # Place in the queue while the work is still waiting; null once it runs
    resource_id: str | None = None  # Set once finished: fetch `GET /resource/{resource_id}` exactly once
    error: str | None = None  # Set when the status is failed


async def get_entity_types(client: httpx.AsyncClient, type_name: str = "full") -> dict[str, str]:
    response = await client.get(f"/entity_types/{type_name}")
    response.raise_for_status()

    return response.json()


async def poll_status(client: httpx.AsyncClient, task_id: str) -> TaskState:
    status = "pending"
    task: dict = {}

    while status == "pending" or status == "running":
        response = await client.get(f"/task/{task_id}")
        response.raise_for_status()

        task = response.json()

        status = task["status"]
        progress = task["progress"]

        print(f"status: {status} progress: {progress} ")
        await asyncio.sleep(0.5)

    return TaskState.model_validate(task)


async def redact_document(client: httpx.AsyncClient, file_path: str, entity_types: dict):
    with open(file_path, "rb") as f:
        # The API reads the `options` form part as one JSON string.
        data = {"options": json.dumps({"entity_types": entity_types, "marked_pdf": True})}
        response = await client.post("/redact/file/async", files={"file": f}, data=data)
        response.raise_for_status()
        response = response.json()

    if not response["task_id"]:
        raise OptionsError("task_id is empty")

    task = await poll_status(client, response["task_id"])

    pdf_file_response = await client.get(f"/resource/{task.resource_id}")
    pdf_file_response.raise_for_status()

    return pdf_file_response.content


async def main(file: str, output: str):
    async with httpx.AsyncClient(
        base_url=API_BASE_URL,
        headers={"Accept": "application/json"},
    ) as client:
        entity_types = await get_entity_types(client)
        pdf = await redact_document(client=client, file_path=file, entity_types=entity_types)

        with open(output, "wb") as f:
            f.write(pdf)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("-f", "--file", required=True)
    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args()

    asyncio.run(main(file=args.file, output=args.output))
