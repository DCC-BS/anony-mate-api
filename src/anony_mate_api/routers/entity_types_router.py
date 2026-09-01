from enum import Enum

from dcc_backend_common.logger import get_logger
from dependency_injector.wiring import inject
from fastapi import APIRouter

logger = get_logger("entity_types_router")


class EntityType(Enum):
    default = "default"
    legal = "legal"


ENTITY_TYPES: dict[EntityType, dict[str, str]] = {
    EntityType.default: {
        "person": "A person, can be first name, last name or lastname and firstname",
        "location": "A location",
    },
    EntityType.legal: {
        "person": "A person, can be first name, last name or lastname and firstname",
        "location": "A location",
        "organization": "A company, institution or other organized group",
        "date": "A calendar date",
        "phone_number": "A telephone number",
    },
}


@inject
def create_router() -> APIRouter:
    logger.info("Creating redact router")
    router: APIRouter = APIRouter(prefix="/entity_types")

    @router.get("/{type_name}")
    async def redact(type_name: EntityType) -> dict[str, str]:
        return ENTITY_TYPES[type_name]

    logger.info("entity types router configured")

    return router
