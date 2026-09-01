"""Task and resource endpoints shared by every long-running operation.

Conversions and redactions both hand back a task id instead of holding the
connection open, so the client polls here rather than waiting through a proxy
that would cut a long request.
"""

from dcc_backend_common.fastapi_error_handling import ApiErrorException
from dcc_backend_common.logger import get_logger
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, status

from anony_mate_api.container import Container
from anony_mate_api.models.error_codes import TASK_NOT_FOUND
from anony_mate_api.models.tasks import TaskState
from anony_mate_api.services.task_store import TaskStore

logger = get_logger("task_router")


@inject
def create_router(task_store: TaskStore = Provide[Container.task_store]) -> APIRouter:
    logger.info("Creating task router")
    router: APIRouter = APIRouter(tags=["tasks"])

    @router.get("/task/{task_id}", summary="Status of a submitted task")
    async def task_state(task_id: str) -> TaskState:
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

    @router.get("/resource/{resource_id}", summary="Collect a finished result (once)")
    async def resource(resource_id: str) -> object:
        """Hand the result over and drop it, so finished work is not kept."""
        found, result = task_store.take_resource(resource_id)
        if not found:
            raise ApiErrorException({
                "errorId": TASK_NOT_FOUND,
                "status": status.HTTP_404_NOT_FOUND,
                "debugMessage": f"Unknown or already collected resource {resource_id}",
            })

        return result

    logger.info("Task router configured")
    return router
