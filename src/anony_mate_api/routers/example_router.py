from collections.abc import AsyncGenerator
from contextlib import aclosing

from dcc_backend_common.logger import get_logger
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Request

from anony_mate_api.container import Container
from anony_mate_api.utils.app_config import AppConfig

logger = get_logger("example_router")


@inject
def create_router(
    config: AppConfig = Provide[Container.app_config],
) -> APIRouter:
    logger.info("Creating example router")
    router: APIRouter = APIRouter(prefix="/example", tags=["example"])

    async def do_something() -> AsyncGenerator[str]:
        yield "test"

    @router.get("/foo")
    async def get_foo(
        request: Request,
    ) -> AsyncGenerator[str]:
        # aclosing: on disconnect the service generator must be closed here,
        # inside the request context, so its cleanup (llm_call usage logging)
        # runs deterministically instead of at garbage collection.
        async with aclosing(do_something()) as chunks:
            async for chunk in chunks:
                if await request.is_disconnected():
                    logger.info("Client disconnected, stopping translation stream")
                    break
                yield chunk

    logger.info("Example router configured")
    return router
