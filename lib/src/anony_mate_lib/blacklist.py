"""Reading the blacklist presets from `GET /blacklist/{type}`."""

from httpx import AsyncClient

from anony_mate_lib.models.blacklist_type import BlacklistType


async def get_blacklist(client: AsyncClient, type_name: BlacklistType = BlacklistType.default) -> list[str]:
    """The blacklist of a preset: words that must never be redacted, e.g.
    country names for `location`."""
    response = await client.get(f"/blacklist/{type_name.value}")
    response.raise_for_status()

    return response.json()
