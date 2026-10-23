"""Collecting a finished job's result from `GET /resource/{resource_id}`."""

from typing import Any

from httpx import AsyncClient


async def get_file_resource(client: AsyncClient, resource_id: str) -> bytes:
    """The result of a finished task as raw bytes, e.g. a marked PDF.

    The resource is deleted once fetched: collect it exactly once.
    """
    response = await client.get(f"/resource/{resource_id}")
    response.raise_for_status()

    return response.content


async def get_json_resource(client: AsyncClient, resource_id: str) -> Any:
    """The result of a finished task, parsed from its JSON body, e.g. a
    redacted text.

    The resource is deleted once fetched: collect it exactly once.
    """
    response = await client.get(f"/resource/{resource_id}")
    response.raise_for_status()

    return response.json()
