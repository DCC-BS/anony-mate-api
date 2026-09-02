from collections.abc import Awaitable, Callable
from typing import Annotated

from dcc_backend_common.logger import get_logger
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Form, Request, UploadFile

from anony_mate_api.container import Container
from anony_mate_api.models.redact_models import (
    DocumentRedactOutput,
    RedactBatchInput,
    RedactFileOptions,
    RedactInput,
    RedactOutput,
)
from anony_mate_api.models.tasks import TaskAccepted
from anony_mate_api.routers._submission import client_key, queue_full_error
from anony_mate_api.services.document_conversion_service import DocumentConversionService
from anony_mate_api.services.redact_service import RedactService
from anony_mate_api.services.task_store import QueueFullError, TaskData, TaskStore

logger = get_logger("redact_router")


def _document_job(
    document_conversion_service: DocumentConversionService,
    redact_service: RedactService,
    upload: tuple[bytes, str, str],
    settings: RedactFileOptions,
) -> Callable[[TaskData], Awaitable[DocumentRedactOutput]]:
    """Build the job that converts one upload and redacts what comes out.

    Each half reports what it can while it runs: conversion says whether
    Docling has started and how many documents are ahead, redaction says how
    far through the text it is.
    """
    content, filename, content_type = upload

    async def run(task: TaskData) -> DocumentRedactOutput:
        def report_conversion(docling_status: str, queue_position: int | None) -> None:
            task.status = "running" if docling_status == "started" else "pending"
            task.queue_position = queue_position
            task.touch()

        converted = await document_conversion_service.convert(
            content,
            filename=filename,
            content_type=content_type,
            on_status=report_conversion,
        )

        def report_redaction(progress: float | None) -> None:
            # Conversion may have left the task waiting on Docling's own queue.
            # Redaction has started, so it is running again and nothing is
            # ahead of it any more.
            task.status = "running"
            task.queue_position = None
            task.progress = progress
            task.touch()

        redacted = await redact_service.redact(
            RedactInput(
                text=converted.text,
                entity_types=settings.entity_types,
                threshold=settings.threshold,
                blacklist=settings.blacklist,
            ),
            report_redaction,
        )

        return DocumentRedactOutput(
            text=converted.text,
            page_offsets=converted.page_offsets,
            redacted_text=redacted.text,
            entities=redacted.entities,
        )

    return run


@inject
def create_router(
    redact_service: RedactService = Provide[Container.redact_service],
    document_conversion_service: DocumentConversionService = Provide[Container.document_conversion_service],
    task_store: TaskStore = Provide[Container.task_store],
) -> APIRouter:
    logger.info("Creating redact router")
    router: APIRouter = APIRouter(prefix="/redact")

    @router.post("/")
    async def redact(payload: RedactInput) -> RedactOutput:
        return await redact_service.redact(payload)

    @router.post("/batch")
    async def redact_batch(payload: RedactBatchInput) -> list[RedactOutput]:
        return await redact_service.redact_batch(payload)

    @router.post("/async", summary="Submit a redaction and poll for it", status_code=202)
    async def redact_async(payload: RedactInput, request: Request) -> TaskAccepted:
        """Accept the text and return at once, so a long scan cannot time out."""

        async def run(task: TaskData) -> RedactOutput:
            def report(progress: float | None) -> None:
                task.progress = progress
                task.touch()

            return await redact_service.redact(payload, report)

        try:
            task = task_store.submit(run, lane="redact", client=client_key(request))
        except QueueFullError as error:
            raise queue_full_error(error) from error

        return TaskAccepted(task_id=task.id)

    @router.post(
        "/file/async",
        summary="Submit a document to be converted and redacted, and poll for it",
        status_code=202,
    )
    async def redact_file_async(
        file: UploadFile,
        options: Annotated[str, Form(description="A RedactFileOptions object, JSON encoded")],
        request: Request,
    ) -> TaskAccepted:
        """Convert an uploaded document and redact it in one submission.

        Only the file and the result travel: a document is the largest thing
        this API handles, and handing the converted text back for the caller to
        submit again would put it through every proxy twice.

        The job holds a conversion slot for its whole life, redaction included.
        Docling has the fewest slots of the two services, and a second lane over
        the same instance would let more conversions run than it can serve.
        """
        settings = RedactFileOptions.model_validate_json(options)
        logger.info("Queueing document redaction", filename=file.filename, size=file.size)

        # The upload is consumed here: the request body is gone by the time the
        # background task runs.
        upload = await document_conversion_service.prepare_upload(file)

        run = _document_job(document_conversion_service, redact_service, upload, settings)

        try:
            task = task_store.submit(run, lane="convert", client=client_key(request))
        except QueueFullError as error:
            raise queue_full_error(error) from error

        return TaskAccepted(task_id=task.id)

    logger.info("redact router configured")
    return router
