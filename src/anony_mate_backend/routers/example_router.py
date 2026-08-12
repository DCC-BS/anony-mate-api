from typing import Annotated

from dcc_backend_common.logger import get_logger
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Request

from anony_mate_backend.container import Container
from anony_mate_backend.utils.configuration import Configuration
from anony_mate_backend.utils.cancel_on_disconnect import CancelOnDisconnect

logger = get_logger("example_router")


@inject
def create_router(
    config: Configuration = Provide[Container.config],
) -> APIRouter:
    logger.info("Creating example router")
    router: APIRouter = APIRouter(prefix="/example", tags=["example"])

    @router.get("/foo")
    async def get_foo(
        request: Request,
    ) -> dict[str, str]:
        # Use CancelOnDisconnect for long-running operations
        async with CancelOnDisconnect(request):
            # Simulate a potentially long operation
            return {"message": f"Example config value is: {config.example}"}

    logger.info("Example router configured")
    return router
