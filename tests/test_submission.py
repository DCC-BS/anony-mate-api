"""Tests for the shared submission helpers used by the queueing endpoints."""

from fastapi import Request
from starlette.datastructures import Headers

from anony_mate_api.models.error_codes import TASK_QUEUE_FULL
from anony_mate_api.routers._submission import CLIENT_HEADER, client_key, queue_full_error
from anony_mate_api.services.task_store import QueueFullError


def _request(headers: dict[str, str] | None = None, host: str = "10.0.0.1") -> Request:
    return Request(
        scope={
            "type": "http",
            "method": "POST",
            "path": "/redact/async",
            "headers": Headers(headers or {}).raw,
            "client": ("10.0.0.1", 1234),
        }
    )


def test_client_key_uses_header_when_present() -> None:
    request = _request({CLIENT_HEADER: "tab-1"})
    assert client_key(request) == "tab-1"


def test_client_key_falls_back_to_peer_address() -> None:
    request = _request()
    assert client_key(request) == "10.0.0.1"


def test_client_key_truncates_overlong_header() -> None:
    request = _request({CLIENT_HEADER: "x" * 200})
    assert len(client_key(request)) == 128


def test_queue_full_error_is_a_429() -> None:
    error = QueueFullError("convert", 16)
    api_error = queue_full_error(error)

    assert api_error.error_response["errorId"] == TASK_QUEUE_FULL
    assert api_error.error_response["status"] == 429
    assert "convert" in api_error.error_response["debugMessage"]
