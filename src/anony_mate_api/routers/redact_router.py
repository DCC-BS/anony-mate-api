from collections.abc import Awaitable, Callable
from typing import Annotated

from dcc_backend_common.fastapi_error_handling import ApiErrorException
from dcc_backend_common.logger import get_logger
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, ValidationError

from anony_mate_api.container import Container
from anony_mate_api.models.error_codes import INVALID_MIME_TYPE
from anony_mate_api.models.marked_pdf import MarkedPdf, PdfAnnotations
from anony_mate_api.models.redact_models import (
    DocumentRedactOutput,
    RedactBatchInput,
    RedactFileOptions,
    RedactInput,
    RedactOutput,
    RedactPdfAnnotateInput,
    RedactPdfOptions,
)
from anony_mate_api.models.tasks import TaskAccepted
from anony_mate_api.routers._submission import client_key, queue_full_error, submit_response
from anony_mate_api.services.document_conversion_service import DocumentConversionService
from anony_mate_api.services.pdf_marking_service import PdfMarkingService
from anony_mate_api.services.redact_service import RedactService
from anony_mate_api.services.task_store import QueueFullError, TaskData, TaskStore

logger = get_logger("redact_router")


def json_form(model: type[BaseModel], description: str) -> Depends:
    """Parse a JSON-encoded form field into ``model``.

    Multipart forms only carry flat name/value pairs, so structured input
    arrives as one JSON string. This keeps the endpoint's parameter typed as
    the model and answers invalid JSON with a 422 instead of a 500.
    """

    async def parse(options: Annotated[str, Form(description=description)]) -> BaseModel:
        try:
            return model.model_validate_json(options)
        except ValidationError as exc:
            raise HTTPException(422, detail=exc.errors(include_url=False)) from exc

    return Depends(parse)


def json_form_openapi(model: type[BaseModel], description: str, example: str) -> dict:
    """OpenAPI for an upload whose ``options`` part is a JSON-encoded ``model``.

    FastAPI sees the part as a plain string, which leaves the docs without a
    schema. This rewrites the request body so the part carries the model's
    schema (as ``application/json`` content) and a prefilled example; the
    matching component schemas are registered in ``app.openapi``.
    """

    return {
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                            "options": {
                                "description": description,
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": f"#/components/schemas/{model.__name__}"},
                                        "example": example,
                                    }
                                },
                            },
                        },
                        "required": ["file"],
                    },
                    "encoding": {"options": {"contentType": "application/json"}},
                }
            },
        },
    }


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


def _marking_job(
    pdf_marking_service: PdfMarkingService,
    upload: tuple[bytes, str, str],
    settings: RedactPdfOptions,
) -> Callable[[TaskData], Awaitable[MarkedPdf | PdfAnnotations]]:
    """Build the job that marks one uploaded PDF for redaction, instead of converting it to text.

    It reports as the text job does: Docling's queue while the layout is read,
    then the detection's progress. By default the job answers with the marks
    alone, for the caller to draw over the file it keeps; with ``marked_pdf``
    set, the marked PDF itself.
    """
    content, filename, content_type = upload
    if content_type != "application/pdf":
        raise ApiErrorException({
            "errorId": INVALID_MIME_TYPE,
            "status": status.HTTP_400_BAD_REQUEST,
            "debugMessage": f"Only a PDF can be returned marked, not {content_type}",
        })

    async def run(task: TaskData) -> MarkedPdf | PdfAnnotations:
        def report_layout(docling_status: str, queue_position: int | None) -> None:
            task.status = "running" if docling_status == "started" else "pending"
            task.queue_position = queue_position
            task.touch()

        def report_detection(progress: float | None) -> None:
            task.status = "running"
            task.queue_position = None
            task.progress = progress
            task.touch()

        return await pdf_marking_service.mark(content, filename, settings, report_layout, report_detection)

    return run


def _annotate_job(
    pdf_marking_service: PdfMarkingService,
    upload: tuple[bytes, str, str],
    payload: RedactPdfAnnotateInput,
) -> Callable[[TaskData], Awaitable[MarkedPdf]]:
    """Build the job that writes a review's marks onto one uploaded PDF."""
    content, filename, _content_type = upload

    async def run(task: TaskData) -> MarkedPdf:
        task.status = "running"
        task.touch()
        return await pdf_marking_service.write_annotations(content, filename, payload.annotations)

    return run


def _submit(
    task_store: TaskStore,
    run: Callable[[TaskData], Awaitable[object]],
    lane: str,
    client: str,
) -> TaskAccepted:
    """Puts a job in its lane and answers with where to poll it.

    All the file routes share it; their differences are in the job, not in
    this exchange.
    """
    try:
        task = task_store.submit(run, lane=lane, client=client)
    except QueueFullError as error:
        raise queue_full_error(error) from error
    return TaskAccepted(task_id=task.id)


@inject
def create_router(
    redact_service: RedactService = Provide[Container.redact_service],
    document_conversion_service: DocumentConversionService = Provide[Container.document_conversion_service],
    pdf_marking_service: PdfMarkingService = Provide[Container.pdf_marking_service],
    task_store: TaskStore = Provide[Container.task_store],
) -> APIRouter:
    logger.info("Creating redact router")
    router: APIRouter = APIRouter(prefix="/anonymize", tags=["anonymize"])

    @router.post("/", summary="Anonymize text")
    async def redact(payload: RedactInput) -> RedactOutput:
        """Redact one text and wait for the result.

        Every detection above the confidence threshold is replaced by a
        `[label:id]` placeholder."""
        return await redact_service.redact(payload)

    @router.post("/batch", summary="Anonymize text batch")
    async def redact_batch(payload: RedactBatchInput) -> list[RedactOutput]:
        """Redact several texts in one call and wait for the results.

        One output per input text, in the same order."""
        return await redact_service.redact_batch(payload)

    @router.post(
        "/async",
        summary="Anonymize text (async)",
        status_code=202,
        responses=submit_response("the redacted text ([`RedactOutput`](#/schemas/RedactOutput))"),
    )
    async def redact_async(payload: RedactInput, request: Request) -> TaskAccepted:
        """Redact a text without waiting for the result.

        Returns a `task_id` (202) for a background job; how to poll and
        collect the result is described under "Tasks and resources" in the
        overview."""

        async def run(task: TaskData) -> RedactOutput:
            def report(progress: float | None) -> None:
                task.progress = progress
                task.touch()

            return await redact_service.redact(payload, report)

        return _submit(task_store, run, "redact", client_key(request))

    @router.post(
        "/file/async",
        summary="Anonymize document (async)",
        status_code=202,
        openapi_extra=json_form_openapi(
            RedactFileOptions,
            "The settings, JSON encoded — every field is described in"
            " [`RedactFileOptions`](#/schemas/RedactFileOptions)",
            '{"entity_types": ["person", "location"], "threshold": 0.8}',
        ),
        responses=submit_response(
            "the document as markdown text with every detection replaced by a `[label:id]`"
            " placeholder ([`DocumentRedactOutput`](#/schemas/DocumentRedactOutput))"
        ),
    )
    async def redact_file_async(
        file: UploadFile,
        options: Annotated[
            RedactFileOptions,
            json_form(
                RedactFileOptions,
                "The settings, JSON encoded — every field is described in"
                " [`RedactFileOptions`](#/schemas/RedactFileOptions)",
            ),
        ],
        request: Request,
    ) -> TaskAccepted:
        """Convert an uploaded document and redact it in one submission.

        The document is converted to markdown text, scanned and redacted; the
        result comes back through the task endpoints as the markdown text with
        every detection replaced by a `[label:id]` placeholder. Upload the
        file as the `file` part; the settings travel as one JSON string in the
        `options` part (an example is shown below).

        For PDFs that should stay PDFs, use `POST /anonymize/pdf/async` instead —
        this endpoint turns them into text.

        The result is collected through the task endpoints — see "Tasks and
        resources" in the overview."""
        logger.info("Queueing document redaction", filename=file.filename, size=file.size)

        # The upload is consumed here: the request body is gone by the time the
        # background task runs.
        upload = await document_conversion_service.prepare_upload(file)

        # The job holds a conversion slot for its whole life, redaction
        # included: Docling has the fewest slots of the two services, and a
        # second lane over the same instance would let more conversions run
        # than it can serve.
        return _submit(
            task_store,
            _document_job(document_conversion_service, redact_service, upload, options),
            "convert",
            client_key(request),
        )

    @router.post(
        "/pdf/async",
        summary="Anonymize PDF (async)",
        status_code=202,
        openapi_extra=json_form_openapi(
            RedactPdfOptions,
            "The settings, JSON encoded — every field is described in [`RedactPdfOptions`](#/schemas/RedactPdfOptions)",
            '{"entity_types": ["person", "location"], "threshold": 0.8, "blacklist": ["Berlin"]}',
        ),
        responses=submit_response(
            """What you get depends on the options you sent:

- **default** — only the detected marks with their page positions
  ([`PdfAnnotationsOutput`](#/schemas/PdfAnnotationsOutput)), for you to draw over the
  file you kept
- **`marked_pdf: true`** — the PDF itself with a redaction mark on every detection, as a
  download; the `X-Mark-Count` header carries the number of marks

Only PDFs are accepted."""
        ),
    )
    async def redact_pdf_async(
        file: UploadFile,
        options: Annotated[
            RedactPdfOptions,
            json_form(
                RedactPdfOptions,
                "The settings, JSON encoded — every field is described in"
                " [`RedactPdfOptions`](#/schemas/RedactPdfOptions)",
            ),
        ],
        request: Request,
    ) -> TaskAccepted:
        """Scan an uploaded PDF for redaction without turning it into text.

        The PDF is read page by page and everything detected is marked. Two
        answers are possible: by default the marks alone come back, with
        their boxes on the page, for you to draw over the file you kept; with
        `marked_pdf: true` the PDF itself comes back with a redaction mark on
        every detection, for review and applying in a PDF editor. Upload the
        file as the `file` part; the settings travel as one JSON string in
        the `options` part (an example is shown below).

        To have a review's marks written onto the PDF, use
        `POST /anonymize/pdf/annotate/async`.

        The result is collected through the task endpoints — see "Tasks and
        resources" in the overview."""
        logger.info("Queueing PDF redaction", filename=file.filename, size=file.size)

        upload = await document_conversion_service.prepare_upload(file)
        return _submit(
            task_store,
            _marking_job(pdf_marking_service, upload, options),
            "convert",
            client_key(request),
        )

    @router.post(
        "/pdf/annotate/async",
        summary="Apply marks to PDF (async)",
        status_code=202,
        openapi_extra=json_form_openapi(
            RedactPdfAnnotateInput,
            "The marks to write, JSON encoded — every field is described in"
            " [`RedactPdfAnnotateInput`](#/schemas/RedactPdfAnnotateInput)",
            (
                '{"annotations": [{"id": "mark-1", "label": "person", "text": "Max Mustermann", '
                '"confidence": 1.0, "boxes": [{"page": 1, "left": 72, "top": 100, "right": 200, "bottom": 116}]}]}'
            ),
        ),
        responses=submit_response("the marked PDF itself, named `<name>.markiert.pdf`"),
    )
    async def annotate_pdf_async(
        file: UploadFile,
        options: Annotated[
            RedactPdfAnnotateInput,
            json_form(
                RedactPdfAnnotateInput,
                "The marks to write, JSON encoded — every field is described in"
                " [`RedactPdfAnnotateInput`](#/schemas/RedactPdfAnnotateInput)",
            ),
        ],
        request: Request,
    ) -> TaskAccepted:
        """Write reviewed marks onto a PDF so it is redacted in the document.

        Send the PDF with the marks a previous review settled on (as the
        `annotations` of the `options` part). The marks are written onto the
        PDF itself — each mark keeps its id, and an overlay text is applied
        where one is given. The redacted PDF comes back as a download named
        `<name>.markiert.pdf`, collected through the task endpoints (see
        "Tasks and resources" in the overview)."""
        logger.info("Queueing annotated PDF", filename=file.filename, size=file.size, marks=len(options.annotations))

        upload = await document_conversion_service.prepare_upload(file)
        return _submit(
            task_store,
            _annotate_job(pdf_marking_service, upload, options),
            "convert",
            client_key(request),
        )

    logger.info("redact router configured")
    return router
