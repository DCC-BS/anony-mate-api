"""Reading the entity type presets from `GET /entity_types/{preset}`."""

from typing import Literal, overload

from httpx import AsyncClient

from anony_mate_lib.models.entity_type import EntityType


@overload
async def get_entity_types(
    client: AsyncClient,
    type_name: EntityType = EntityType.default,
    *,
    verbose: Literal[False] = False,
) -> dict[str, str]: ...


@overload
async def get_entity_types(
    client: AsyncClient,
    type_name: EntityType = EntityType.default,
    *,
    verbose: Literal[True],
) -> dict[str, dict[str, str]]: ...


async def get_entity_types(
    client: AsyncClient,
    type_name: EntityType = EntityType.default,
    *,
    verbose: bool = False,
) -> dict[str, str] | dict[str, dict[str, str]]:
    """The entity types of a preset, as `label: description`, ready to pass as
    `entity_types` when redacting. With ``verbose``, each entry instead carries
    the type's display ``name`` beside its ``description``."""
    response = await client.get(f"/entity_types/{type_name.value}", params={"verbose": verbose} if verbose else None)
    response.raise_for_status()

    return response.json()
