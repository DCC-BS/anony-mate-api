"""Shared helpers for the endpoints that queue work.

Both submitting endpoints need the same two things: who is asking, so the queue
can serve clients in turn, and a sensible answer when the queue is full.
"""

from dcc_backend_common.fastapi_error_handling import ApiErrorException
from dcc_backend_common.logger import get_logger
from fastapi import Request, status

from anony_mate_api.models.error_codes import TASK_QUEUE_FULL
from anony_mate_api.services.task_store import QueueFullError

logger = get_logger("submission")

#: Sent by the frontend so several tabs on one machine are one client.
CLIENT_HEADER = "X-Client-Id"


def client_key(request: Request) -> str:
    """Identify the submitter, for the queue's round-robin between clients.

    Falls back to the peer address, which is enough to keep one busy browser
    from starving another even when the header is absent.
    """
    header = request.headers.get(CLIENT_HEADER)
    if header:
        return header[:128]

    return request.client.host if request.client else "anonymous"


def queue_full_error(error: QueueFullError) -> ApiErrorException:
    """Turn a full queue into a 429 the caller can retry."""
    logger.warning("Rejecting submission, queue full", lane=error.lane, max_queued=error.max_queued)
    return ApiErrorException({
        "errorId": TASK_QUEUE_FULL,
        "status": status.HTTP_429_TOO_MANY_REQUESTS,
        "debugMessage": str(error),
    })


def submit_response(result: str) -> dict:
    """OpenAPI ``responses`` for an endpoint that queues a task.

    Documents the real exchange: the endpoint answers with a ``task_id``;
    the description names what the task's result will be, to collect from
    the resource endpoint once the task is ``finished``. The link jumps
    straight to polling the new task with the id from the response body.
    """
    return {
        "202": {
            "description": (
                "Accepted; the result is not ready yet.\n\n"
                "Poll `GET /task/{task_id}` until it is `finished`, then collect the result once"
                " from `GET /resource/{resource_id}`:\n\n"
                f"{result}"
            ),
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/TaskAccepted"}}},
            "links": {
                "PollTask": {
                    "operationId": "get_task_status",
                    "parameters": {"task_id": "$response.body#/task_id"},
                    "description": "Poll the submitted task's status with the returned `task_id`.",
                }
            },
        }
    }


def resource_response(*json_refs: str, pdf: bool = False) -> dict:
    """OpenAPI ``responses`` for what the resource endpoint can hand out.

    Each ref is the component schema of a possible JSON result; with
    ``pdf`` the result may also be the PDF file itself.
    """
    content: dict = {}
    if json_refs:
        content["application/json"] = {
            "schema": {"oneOf": [{"$ref": f"#/components/schemas/{ref}"} for ref in json_refs]}
        }
    if pdf:
        content["application/pdf"] = {"schema": {"type": "string", "format": "binary"}}
    return {
        "200": {
            "description": (
                "The result of the finished task, as documented on the endpoint that submitted"
                " it; delivered once, then deleted."
            ),
            "content": content,
        }
    }
