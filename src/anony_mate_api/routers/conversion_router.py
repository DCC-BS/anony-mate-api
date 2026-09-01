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
from anony_mate_api.models.tasks import TaskAccepted
from anony_mate_api.services.document_conversion_service import DocumentConversionService
from anony_mate_api.services.task_store import TaskData, TaskStore

logger = get_logger("conversion_router")


@inject
def create_router(
    document_conversion_service: DocumentConversionService = Provide[Container.document_conversion_service],
    task_store: TaskStore = Provide[Container.task_store],
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

    @router.post(
        "/doc/async",
        summary="Submit a document conversion and poll for it",
        status_code=202,
    )
    async def convert_async(file: UploadFile) -> TaskAccepted:
        """Accept the upload and return at once, so a slow OCR cannot time out."""
        logger.info("Queueing document conversion", filename=file.filename, size=file.size)

        # The upload is consumed here: the request body is gone by the time the
        # background task runs.
        content, filename, content_type = await document_conversion_service.prepare_upload(file)

        async def run(task: TaskData) -> ConversionResult:
            def report(docling_status: str) -> None:
                # One task is one document, so there is no fraction to report;
                # what a caller can use is whether docling has started on it.
                task.status = "running" if docling_status == "started" else "pending"
                task.touch()

            return await document_conversion_service.convert(
                content,
                filename=filename,
                content_type=content_type,
                on_status=report,
            )

        return TaskAccepted(task_id=task_store.submit(run).id)

    logger.info("Conversion router configured")
    return router
