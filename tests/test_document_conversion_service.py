"""Tests for the document conversion service.

The pure helpers are exercised directly; the HTTP flow runs against an
``httpx.MockTransport`` so no Docling server is needed. The suite has no async
plugin, so each test drives its own event loop.
"""

import asyncio
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from dcc_backend_common.fastapi_error_handling import ApiErrorException
from starlette.datastructures import UploadFile

from anony_mate_api.models.error_codes import (
    DOCUMENT_CONVERSION_ERROR,
    FILE_TOO_LARGE,
    INVALID_MIME_TYPE,
)
from anony_mate_api.services.document_conversion_service import (
    DocumentConversionService,
    get_mimetype,
    split_pages,
    validate_mimetype,
)
from anony_mate_api.utils.app_config import AppConfig


def test_split_pages_returns_offsets() -> None:
    text, offsets = split_pages("a<!-- docling-page -->b<!-- docling-page -->c")
    assert text == "abc"
    assert offsets == [0, 1, 2]


def test_split_pages_single_page() -> None:
    text, offsets = split_pages("only one page")
    assert text == "only one page"
    assert offsets == [0]


def test_get_mimetype_known_extensions() -> None:
    assert get_mimetype(Path("doc.pdf")) == "application/pdf"
    assert get_mimetype(Path("doc.DOCX")) == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert get_mimetype(Path("doc.md")) == "text/markdown"
    assert get_mimetype(Path("doc.txt")) == "text/plain"


def test_get_mimetype_unknown_extension_is_invalid() -> None:
    assert get_mimetype(Path("doc.xyz")) == "invalid"


def test_validate_mimetype_accepts_known() -> None:
    validate_mimetype("application/pdf", logger_context={})


def test_validate_mimetype_rejects_empty() -> None:
    with pytest.raises(ApiErrorException) as excinfo:
        validate_mimetype("", logger_context={})
    assert excinfo.value.error_response["errorId"] == INVALID_MIME_TYPE


def test_validate_mimetype_rejects_unknown() -> None:
    with pytest.raises(ApiErrorException) as excinfo:
        validate_mimetype("application/octet-stream", logger_context={})
    assert excinfo.value.error_response["errorId"] == INVALID_MIME_TYPE


def _config() -> AppConfig:
    return AppConfig(
        client_url="http://localhost:3000",
        llm_api_key="none",
        llm_url="http://localhost:8001/v1",
        llm_model="test-model",
        llm_health_check_url="http://localhost:8001/health",
        gliner_api_base_url="http://gliner.test",
        gliner_api_key="test-key",
        gliner_use_binary_upload=False,
        gliner_use_async_tasks=False,
        docling_url="http://docling.test/v1",
        docling_api_key="test-key",
        docling_poll_interval_seconds=0.0,
        docling_conversion_timeout_seconds=60.0,
    )


def _service(handler) -> DocumentConversionService:
    return DocumentConversionService(_config(), transport=httpx.MockTransport(handler))


def _json_response(body: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body)


def test_convert_happy_path_returns_markdown() -> None:
    """Submit, poll to success, then fetch the result and split page markers."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/v1/convert/file/async":
            return _json_response({"task_id": "task-1"})
        if request.url.path == "/v1/status/poll/task-1":
            return _json_response({"task_status": "success"})
        if request.url.path == "/v1/result/task-1":
            return _json_response({"document": {"md_content": "a<!-- docling-page -->b"}})
        raise AssertionError(f"unexpected path {request.url.path}")

    async def scenario() -> tuple[str, list[int]]:
        service = _service(handler)
        try:
            result = await service.convert(BytesIO(b"pdf-bytes"), filename="doc.pdf")
            return result.text, result.page_offsets
        finally:
            await service.close()

    text, offsets = asyncio.run(scenario())
    assert text == "ab"
    assert offsets == [0, 1]
    assert calls == ["/v1/convert/file/async", "/v1/status/poll/task-1", "/v1/result/task-1"]


def test_convert_polls_until_success() -> None:
    """A task still pending is polled again rather than returned early."""
    polls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/convert/file/async":
            return _json_response({"task_id": "task-1"})
        if request.url.path == "/v1/status/poll/task-1":
            polls["count"] += 1
            if polls["count"] == 1:
                return _json_response({"task_status": "pending", "task_position": 3})
            return _json_response({"task_status": "success"})
        if request.url.path == "/v1/result/task-1":
            return _json_response({"document": {"md_content": "done"}})
        raise AssertionError(f"unexpected path {request.url.path}")

    async def scenario() -> None:
        service = _service(handler)
        try:
            _ = await service.convert(BytesIO(b"pdf-bytes"), filename="doc.pdf")
        finally:
            await service.close()

    asyncio.run(scenario())
    assert polls["count"] == 2


def test_convert_reports_status_and_position() -> None:
    statuses: list[tuple[str, int | None]] = []
    polls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/convert/file/async":
            return _json_response({"task_id": "task-1"})
        if request.url.path == "/v1/status/poll/task-1":
            polls["count"] += 1
            if polls["count"] == 1:
                return _json_response({"task_status": "pending", "task_position": 2})
            return _json_response({"task_status": "success"})
        if request.url.path == "/v1/result/task-1":
            return _json_response({"document": {"md_content": "done"}})
        raise AssertionError(f"unexpected path {request.url.path}")

    async def scenario() -> None:
        service = _service(handler)
        try:
            _ = await service.convert(
                BytesIO(b"pdf-bytes"),
                filename="doc.pdf",
                on_status=lambda s, p: statuses.append((s, p)),
            )
        finally:
            await service.close()

    asyncio.run(scenario())
    assert statuses == [("pending", 2), ("success", None)]


def test_convert_raises_when_task_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/convert/file/async":
            return _json_response({"task_id": "task-1"})
        if request.url.path == "/v1/status/poll/task-1":
            return _json_response({"task_status": "failure", "error_message": "ocr died"})
        raise AssertionError(f"unexpected path {request.url.path}")

    async def scenario() -> None:
        service = _service(handler)
        try:
            with pytest.raises(ApiErrorException) as excinfo:
                _ = await service.convert(BytesIO(b"pdf-bytes"), filename="doc.pdf")
            assert excinfo.value.error_response["errorId"] == DOCUMENT_CONVERSION_ERROR
            assert "ocr died" in excinfo.value.error_response["debugMessage"]
        finally:
            await service.close()

    asyncio.run(scenario())


def test_convert_raises_when_task_id_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({})

    async def scenario() -> None:
        service = _service(handler)
        try:
            with pytest.raises(ApiErrorException) as excinfo:
                _ = await service.convert(BytesIO(b"pdf-bytes"), filename="doc.pdf")
            assert excinfo.value.error_response["errorId"] == DOCUMENT_CONVERSION_ERROR
        finally:
            await service.close()

    asyncio.run(scenario())


def test_convert_raises_on_docling_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    async def scenario() -> None:
        service = _service(handler)
        try:
            with pytest.raises(ApiErrorException) as excinfo:
                _ = await service.convert(BytesIO(b"pdf-bytes"), filename="doc.pdf")
            assert excinfo.value.error_response["errorId"] == DOCUMENT_CONVERSION_ERROR
        finally:
            await service.close()

    asyncio.run(scenario())


def test_prepare_upload_rejects_oversized() -> None:
    config = _config()
    config.max_upload_bytes = 10
    service = DocumentConversionService(config, transport=httpx.MockTransport(lambda r: _json_response({})))

    async def scenario() -> None:
        file = UploadFile(filename="doc.pdf", file=BytesIO(b"x" * 100))
        file.size = 100
        with pytest.raises(ApiErrorException) as excinfo:
            _ = await service.prepare_upload(file)
        assert excinfo.value.error_response["errorId"] == FILE_TOO_LARGE

    asyncio.run(scenario())
