"""The async client for the AnonyMate API.

A run looks like::

    async with AnonyMateClient("http://localhost:8000") as client:
        result = await client.anonymize_text(RedactInput(text="Anna wohnt in Berlin."))

Entering loads the `default` presets of entity types and blacklist, so the
redaction calls work without any settings of their own; `add_entity_type` and
`add_to_blacklist` extend them, `load_entity_types` and `load_blacklist`
replace them with another preset. Everything else the API can do is one
method: redact a text, redact an uploaded document or PDF, write reviewed
marks onto a PDF. The async submissions are waited on here — the polling of
the task and the one-time collection of the result are part of the call.
"""

from collections.abc import Sequence
from typing import TypeVar

from httpx import AsyncBaseTransport, AsyncClient
from pydantic import BaseModel

from anony_mate_lib.blacklist import get_blacklist
from anony_mate_lib.entities import get_entity_types
from anony_mate_lib.errors import MissingResourceError
from anony_mate_lib.models.blacklist_type import BlacklistType
from anony_mate_lib.models.entity_type import EntityType
from anony_mate_lib.models.redact_models import (
    DocumentRedactOutput,
    MarkAnnotationInput,
    PdfAnnotationsOutput,
    RedactBatchInput,
    RedactFileOptions,
    RedactInput,
    RedactionSettings,
    RedactOutput,
    RedactPdfAnnotateInput,
    RedactPdfOptions,
)
from anony_mate_lib.models.tasks import TaskAccepted
from anony_mate_lib.resources import get_file_resource, get_json_resource
from anony_mate_lib.tasks import poll_status

SettingsT = TypeVar("SettingsT", bound=RedactionSettings)
ResultT = TypeVar("ResultT", bound=BaseModel)

#: How long one HTTP call may take before it gives up. The async submissions
#: answer at once; what runs long waits behind the task polling, which is made
#: of short calls again.
DEFAULT_TIMEOUT = 60.0


class AnonyMateClient:
    """Async client for one AnonyMate API, with the presets at hand.

    Use it as an async context manager; on entering it loads the `default`
    entity types and blacklist from the API, so the redaction calls need no
    settings of their own.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        poll_interval: float = 0.5,
        transport: AsyncBaseTransport | None = None,
    ):
        self._client = AsyncClient(base_url=base_url, timeout=timeout, transport=transport)
        self._poll_interval = poll_interval
        #: The entity types the redaction calls fall back to, as `label: description`.
        self.entity_types: dict[str, str] = {}
        #: The words the redaction calls protect from being redacted.
        self.blacklist: list[str] = []

    async def __aenter__(self) -> "AnonyMateClient":
        await self.load_entity_types()
        await self.load_blacklist()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()

    async def load_entity_types(self, preset: EntityType = EntityType.default) -> None:
        """Replace the entity types with the preset's, as `GET /entity_types/{preset}` serves it."""
        self.entity_types = await get_entity_types(self._client, preset)

    async def load_blacklist(self, preset: BlacklistType = BlacklistType.default) -> None:
        """Replace the blacklist with the preset's, as `GET /blacklist/{preset}` serves it."""
        self.blacklist = await get_blacklist(self._client, preset)

    def add_entity_type(self, label: str, description: str) -> None:
        """Redact one more entity type, described in the words the model reads."""
        self.entity_types[label] = description

    def add_to_blacklist(self, word: str) -> None:
        """Protect one more word from being redacted."""
        self.blacklist.append(word)

    async def anonymize_text(self, options: RedactInput) -> RedactOutput:
        """Redact one text and wait for the result, `POST /anonymize/`.

        Settings left empty fall back to the client's presets.
        """
        response = await self._client.post("/anonymize/", json=self._with_presets(options).model_dump(mode="json"))
        response.raise_for_status()

        return RedactOutput.model_validate(response.json())

    async def anonymize_texts(self, options: RedactBatchInput) -> list[RedactOutput]:
        """Redact several texts in one call and wait for the results,
        `POST /anonymize/batch`. One output per input text, in the same order.

        Settings left empty fall back to the client's presets.
        """
        response = await self._client.post("/anonymize/batch", json=self._with_presets(options).model_dump(mode="json"))
        response.raise_for_status()

        return [RedactOutput.model_validate(text) for text in response.json()]

    async def anonymize_document(
        self,
        content: bytes,
        filename: str,
        *,
        options: RedactFileOptions | None = None,
    ) -> DocumentRedactOutput:
        """Convert an uploaded document (Word, PDF, …) to markdown and redact
        it, `POST /anonymize/file/async`. The result is the markdown text with
        every detection replaced by a `[label:id]` placeholder.

        Settings left empty fall back to the client's presets.
        """
        settings = self._with_presets(options if options is not None else RedactFileOptions())
        task_id = await self._submit_upload("/anonymize/file/async", content, filename, settings)
        return await self._collect_model(task_id, DocumentRedactOutput)

    async def anonymize_pdf(
        self,
        content: bytes,
        filename: str,
        *,
        options: RedactPdfOptions | None = None,
    ) -> bytes | PdfAnnotationsOutput:
        """Scan an uploaded PDF for redaction without turning it into text,
        `POST /anonymize/pdf/async`.

        What comes back depends on `marked_pdf`: with it, the PDF itself with
        a redaction mark on every detection, as bytes for an editor to review
        and apply; without it — the default — the detected marks with their
        page positions, as `PdfAnnotationsOutput`, for you to draw over the
        file you kept.

        Settings left empty fall back to the client's presets.
        """
        settings = self._with_presets(options if options is not None else RedactPdfOptions())
        task_id = await self._submit_upload("/anonymize/pdf/async", content, filename, settings)
        if settings.marked_pdf:
            return await self._collect_file(task_id)
        return await self._collect_model(task_id, PdfAnnotationsOutput)

    async def annotate_pdf(
        self,
        content: bytes,
        filename: str,
        annotations: Sequence[MarkAnnotationInput],
    ) -> bytes:
        """Write a review's marks onto the PDF itself, so the redaction
        happens in the document, `POST /anonymize/pdf/annotate/async`.

        Returns the PDF with every mark written as a /Redact annotation.
        """
        payload = RedactPdfAnnotateInput(annotations=list(annotations))
        task_id = await self._submit_upload("/anonymize/pdf/annotate/async", content, filename, payload)
        return await self._collect_file(task_id)

    def _with_presets[SettingsT: RedactionSettings](self, options: SettingsT) -> SettingsT:
        """The settings with the client's presets filled in where they are empty."""
        filled = options.model_copy()
        if not filled.entity_types:
            filled.entity_types = dict(self.entity_types)
        if not filled.blacklist:
            filled.blacklist = list(self.blacklist)
        return filled

    async def _submit_upload(
        self,
        path: str,
        content: bytes,
        filename: str,
        options: RedactionSettings | RedactPdfAnnotateInput,
    ) -> str:
        """Upload one file with its JSON-encoded settings and answer the task id to poll."""
        response = await self._client.post(
            path,
            files={"file": (filename, content)},
            data={"options": options.model_dump_json()},
        )
        response.raise_for_status()

        accepted = TaskAccepted.model_validate(response.json())
        return accepted.task_id

    async def _collect_model(self, task_id: str, model: type[ResultT]) -> ResultT:
        """Wait the task out and read its JSON result, collected exactly once."""
        task = await poll_status(self._client, task_id, interval=self._poll_interval)
        if not task.resource_id:
            raise MissingResourceError(f"task {task_id} finished without a resource id to fetch the result under")
        return model.model_validate(await get_json_resource(self._client, task.resource_id))

    async def _collect_file(self, task_id: str) -> bytes:
        """Wait the task out and fetch its file result, collected exactly once."""
        task = await poll_status(self._client, task_id, interval=self._poll_interval)
        if not task.resource_id:
            raise MissingResourceError(f"task {task_id} finished without a resource id to fetch the result under")
        return await get_file_resource(self._client, task.resource_id)
