from dcc_backend_common.logger import get_logger
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter

from anony_mate_api.container import Container
from anony_mate_api.models.redact_models import RedactInput, RedactOutput
from anony_mate_api.services.redact_service import RedactService

logger = get_logger("redact_router")


@inject
def create_router(redact_service: RedactService = Provide[Container.redact_service]) -> APIRouter:
    logger.info("Creating redact router")
    router: APIRouter = APIRouter(prefix="/redact")

    @router.post("/")
    async def redact(input: RedactInput) -> RedactOutput:
        return await redact_service.redact(input)

    logger.info("redact router configured")
    return router
