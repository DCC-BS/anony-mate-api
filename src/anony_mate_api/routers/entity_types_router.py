from enum import Enum

from dcc_backend_common.logger import get_logger
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter

from anony_mate_api.container import Container
from anony_mate_api.services.redact_service import RedactService

logger = get_logger("entity_types_router")


class EntityType(Enum):
    default = "default"
    legal = "legal"


@inject
def create_router(redact_service: RedactService = Provide[Container.redact_service]) -> APIRouter:
    logger.info("Creating redact router")
    router: APIRouter = APIRouter(prefix="/entity_types")

    @router.post("/{type_name}")
    async def redact(type_name: EntityType) -> dict[str, str]:
        return {"person": "A person, can be first name, last name or lastname and firstname", "location": "A location"}

    logger.info("entity types router configured")

    return router
