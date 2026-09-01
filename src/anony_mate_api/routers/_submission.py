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
