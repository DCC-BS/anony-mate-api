from dcc_backend_common.logger import get_logger
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter

from anony_mate_api.container import Container
from anony_mate_api.models.redact_models import RedactBatchInput, RedactInput, RedactOutput
from anony_mate_api.models.tasks import TaskAccepted
from anony_mate_api.services.redact_service import RedactService
from anony_mate_api.services.task_store import TaskData, TaskStore

logger = get_logger("redact_router")


@inject
def create_router(
    redact_service: RedactService = Provide[Container.redact_service],
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
    async def redact_async(payload: RedactInput) -> TaskAccepted:
        """Accept the text and return at once, so a long scan cannot time out."""

        async def run(task: TaskData) -> RedactOutput:
            def report(progress: float | None) -> None:
                task.progress = progress
                task.touch()

            return await redact_service.redact(payload, report)

        return TaskAccepted(task_id=task_store.submit(run).id)

    logger.info("redact router configured")
    return router
