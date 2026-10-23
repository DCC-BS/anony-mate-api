"""The client library against a mocked transport: the wire it speaks is the API's.

The handlers answer the way the routers do: presets on `GET`, a `202` with a
task id on the async submissions, a state on `GET /task/{id}` and the result
on `GET /resource/{id}`, collected exactly once.
"""

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest

from anony_mate_lib import (
    AnonyMateClient,
    MissingResourceError,
    PdfAnnotationsOutput,
    RedactInput,
    RedactPdfOptions,
    TaskFailedError,
)

BASE_URL = "http://anonymate.test"

ENTITY_TYPES = {"person": "A person's name", "ort": "A place"}
BLACKLIST = ["Deutschland"]

REDACT_OUTPUT = {
    "text": "[person:1] wohnt in [ort:1].",
    "entities": {
        "person": [
            {"id": "person-1", "text": "Anna", "label": "person", "confidence": 0.95, "start": 0, "end": 4},
        ],
        "ort": [],
    },
}

PDF_BYTES = b"%PDF-1.7 mocked marked pdf"

ANNOTATIONS_OUTPUT = {
    "annotations": [
        {
            "id": "person-1",
            "label": "person",
            "text": "Anna",
            "confidence": 0.95,
            "boxes": [{"page": 1, "left": 72.0, "top": 80.0, "right": 110.0, "bottom": 96.0}],
        },
    ],
    "page_sizes": {"1": [595.0, 842.0]},
}


def api(handler: Callable[[httpx.Request], httpx.Response]) -> AnonyMateClient:
    """A client that talks to the mocked API, without polling pauses."""
    return AnonyMateClient(BASE_URL, poll_interval=0.0, transport=httpx.MockTransport(handler))


def test_presets_fill_the_redact_defaults():
    """Entering loads the default presets; a text redacted without settings carries them."""
    posts = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/entity_types/default":
            return httpx.Response(200, json=ENTITY_TYPES)
        if request.method == "GET" and request.url.path == "/blacklist/default":
            return httpx.Response(200, json=BLACKLIST)
        if request.method == "POST" and request.url.path == "/anonymize/":
            posts.append(json.loads(request.content))
            return httpx.Response(200, json=REDACT_OUTPUT)
        return httpx.Response(404)

    async def run() -> None:
        async with api(handler) as client:
            assert client.entity_types == ENTITY_TYPES
            assert client.blacklist == BLACKLIST

            client.add_entity_type("steuernummer", "A German tax number")
            client.add_to_blacklist("Berlin")
            result = await client.anonymize_text(RedactInput(text="Anna wohnt in Berlin."))

        assert result.text == REDACT_OUTPUT["text"]
        assert [e.text for e in result.entities["person"]] == ["Anna"]
        payload = posts[0]
        assert payload["entity_types"] == {**ENTITY_TYPES, "steuernummer": "A German tax number"}
        assert payload["blacklist"] == [*BLACKLIST, "Berlin"]

    asyncio.run(run())


def test_pdf_redaction_returns_the_marked_pdf():
    """With `marked_pdf`, the bytes of the PDF itself come back, after polling."""
    states = iter(["pending", "finished"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/anonymize/pdf/async":
            body = request.content
            assert b'name="file"' in body and b"brief.pdf" in body
            assert b'name="options"' in body
            options_json = body.split(b'name="options"\r\n\r\n', 1)[1].split(b"\r\n--", 1)[0]
            assert json.loads(options_json) == {
                "entity_types": ENTITY_TYPES,
                "threshold": 0.8,
                "blacklist": BLACKLIST,
                "marked_pdf": True,
            }
            return httpx.Response(202, json={"task_id": "task-1"})
        if request.method == "GET" and request.url.path == "/task/task-1":
            status = next(states)
            return httpx.Response(
                200,
                json={
                    "task_id": "task-1",
                    "status": status,
                    "resource_id": "resource-1" if status == "finished" else None,
                    "progress": 1.0 if status == "finished" else None,
                },
            )
        if request.method == "GET" and request.url.path == "/resource/resource-1":
            return httpx.Response(200, content=PDF_BYTES)
        return httpx.Response(404)

    async def run() -> None:
        # The presets are already loaded, so entering skips straight to the upload.
        client = api(handler)
        client.entity_types = ENTITY_TYPES
        client.blacklist = BLACKLIST
        try:
            pdf = await client.anonymize_pdf(b"%PDF-1.7 brief", "brief.pdf", options=RedactPdfOptions(marked_pdf=True))
        finally:
            await client.aclose()

        assert pdf == PDF_BYTES

    asyncio.run(run())


def test_pdf_redaction_returns_the_annotations_by_default():
    """Without `marked_pdf`, the marks with their page positions come back, parsed."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/anonymize/pdf/async":
            assert b'"marked_pdf":false' in request.content
            return httpx.Response(202, json={"task_id": "task-1"})
        if request.method == "GET" and request.url.path == "/task/task-1":
            return httpx.Response(200, json={"task_id": "task-1", "status": "finished", "resource_id": "r-1"})
        if request.method == "GET" and request.url.path == "/resource/r-1":
            return httpx.Response(200, json=ANNOTATIONS_OUTPUT)
        return httpx.Response(404)

    async def run() -> None:
        client = api(handler)
        client.entity_types = ENTITY_TYPES
        client.blacklist = BLACKLIST
        try:
            result = await client.anonymize_pdf(b"%PDF-1.7 brief", "brief.pdf")
        finally:
            await client.aclose()

        assert isinstance(result, PdfAnnotationsOutput)
        assert result.annotations[0].id == "person-1"
        assert result.annotations[0].boxes[0].page == 1
        assert result.page_sizes == {"1": [595.0, 842.0]}

    asyncio.run(run())


def test_a_failed_task_raises_with_its_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/anonymize/pdf/async":
            return httpx.Response(202, json={"task_id": "task-1"})
        if request.method == "GET" and request.url.path == "/task/task-1":
            return httpx.Response(200, json={"task_id": "task-1", "status": "failed", "error": "docling is down"})
        return httpx.Response(404)

    async def run() -> None:
        client = api(handler)
        try:
            with pytest.raises(TaskFailedError, match="docling is down"):
                await client.anonymize_pdf(b"%PDF-1.7 brief", "brief.pdf")
        finally:
            await client.aclose()

    asyncio.run(run())


def test_a_finished_task_without_a_result_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/anonymize/pdf/async":
            return httpx.Response(202, json={"task_id": "task-1"})
        if request.method == "GET" and request.url.path == "/task/task-1":
            return httpx.Response(200, json={"task_id": "task-1", "status": "finished", "resource_id": None})
        return httpx.Response(404)

    async def run() -> None:
        client = api(handler)
        try:
            with pytest.raises(MissingResourceError, match="task-1"):
                await client.anonymize_pdf(b"%PDF-1.7 brief", "brief.pdf")
        finally:
            await client.aclose()

    asyncio.run(run())
