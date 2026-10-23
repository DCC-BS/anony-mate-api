"""Task and resource endpoints shared by every long-running operation.

Conversions and redactions both hand back a task id instead of holding the
connection open, so the client polls here rather than waiting through a proxy
that would cut a long request.
"""

from dcc_backend_common.fastapi_error_handling import ApiErrorException
from dcc_backend_common.logger import get_logger
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Response, status

from anony_mate_api.container import Container
from anony_mate_api.models.error_codes import TASK_NOT_FOUND
from anony_mate_api.models.marked_pdf import MarkedPdf, PdfAnnotations
from anony_mate_api.models.redact_models import PdfAnnotationsOutput
from anony_mate_api.models.tasks import TaskState
from anony_mate_api.routers._submission import resource_response
from anony_mate_api.services.task_store import TaskStore

logger = get_logger("task_router")


@inject
def create_router(task_store: TaskStore = Provide[Container.task_store]) -> APIRouter:
    logger.info("Creating task router")
    router: APIRouter = APIRouter(tags=["tasks"])

    @router.get(
        "/task/{task_id}",
        summary="Task status",
        operation_id="get_task_status",
        responses={
            "200": {
                "description": (
                    "The task's current state; when `finished`, the `resource_id` names where to collect the result."
                ),
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/TaskState"}}},
                "links": {
                    "CollectResult": {
                        "operationId": "get_task_result",
                        "parameters": {"resource_id": "$response.body#/resource_id"},
                        "description": "Collect the result with the returned `resource_id`, once the task is `finished`.",
                    }
                },
            }
        },
    )
    async def task_state(task_id: str) -> TaskState:
        """Status of a submitted task: `pending` or `running` with progress or
        queue position while it works, `finished` with a `resource_id` once
        the result is ready, `failed` with an error otherwise. The full
        lifecycle is described under "Tasks and resources" in the overview."""
        task = task_store.poll(task_id)
        if task is None:
            raise ApiErrorException({
                "errorId": TASK_NOT_FOUND,
                "status": status.HTTP_404_NOT_FOUND,
                "debugMessage": f"Unknown task {task_id}",
            })

        return TaskState(
            task_id=task.id,
            status=task.status,
            progress=task.progress,
            queue_position=task.queue_position,
            resource_id=task.resource_id,
            error=task.error,
        )

    @router.get(
        "/resource/{resource_id}",
        summary="Fetch task result",
        operation_id="get_task_result",
        responses=resource_response(
            "RedactOutput",
            "DocumentRedactOutput",
            "ConversionResult",
            "PdfAnnotationsOutput",
            pdf=True,
        ),
    )
    async def resource(resource_id: str) -> object:
        """Collect the result of a finished task, once.

        The result is delivered exactly this one time and then deleted. Redacted
        text and document conversions come back as JSON; a marked PDF comes
        back as the PDF file itself, with the number of marks on it in the
        `X-Mark-Count` header."""
        found, result = task_store.take_resource(resource_id)
        if not found:
            raise ApiErrorException({
                "errorId": TASK_NOT_FOUND,
                "status": status.HTTP_404_NOT_FOUND,
                "debugMessage": f"Unknown or already collected resource {resource_id}",
            })

        if isinstance(result, MarkedPdf):
            return Response(
                content=result.content,
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f'attachment; filename="{result.filename}"',
                    "X-Mark-Count": str(result.marks),
                },
            )

        if isinstance(result, PdfAnnotations):
            # The marks as the contract reads them: every box a named thing,
            # not a bare array a caller has to know the order of.
            return PdfAnnotationsOutput.of(result)

        return result

    logger.info("Task router configured")
    return router
