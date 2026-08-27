"""Document conversion API router.

Accepts an uploaded document (PDF, DOCX, images, ...) and returns its content as
markdown text, ready to be redacted.
"""

import asyncio

from dcc_backend_common.logger import get_logger
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Request, UploadFile

from anony_mate_api.container import Container
from anony_mate_api.models.conversion import ConversionResult
from anony_mate_api.services.document_converstion_service import DocumentConversionService

logger = get_logger("conversion_router")


@inject
def create_router(
    document_conversion_service: DocumentConversionService = Provide[Container.document_conversion_service],
) -> APIRouter:
    logger.info("Creating conversion router")
    router: APIRouter = APIRouter(prefix="/convert", tags=["convert"])

    @router.post("/doc", summary="Convert an uploaded document to markdown text")
    async def convert(request: Request, file: UploadFile) -> ConversionResult:
        logger.info("Converting document", filename=file.filename, content_type=file.content_type, size=file.size)

        task = asyncio.create_task(document_conversion_service.convert(file))

        while not task.done():
            await asyncio.sleep(0.1)
            if await request.is_disconnected():
                _ = task.cancel()
                logger.info("Conversion cancelled because the client disconnected", filename=file.filename)
                return ConversionResult(text="")

        return task.result()

    logger.info("Conversion router configured")
    return router
