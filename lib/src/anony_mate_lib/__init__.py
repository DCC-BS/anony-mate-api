"""AnonyMate client library: redact personal data from texts and documents over the API.

    async with AnonyMateClient("http://localhost:8000") as client:
        result = await client.anonymize_text(RedactInput(text="Anna wohnt in Berlin."))

The client loads the API's `default` presets of entity types and blacklist on
entering, so the redaction calls work without settings of their own. The
models live in `anony_mate_lib.models`; the building blocks the client itself
is made of — preset and task polling helpers for use with an own `httpx`
client — one level below: `anony_mate_lib.entities`, `anony_mate_lib.blacklist`,
`anony_mate_lib.tasks`, `anony_mate_lib.resources`.
"""

from anony_mate_lib.anony_mate_client import DEFAULT_TIMEOUT, AnonyMateClient
from anony_mate_lib.blacklist import get_blacklist
from anony_mate_lib.entities import get_entity_types
from anony_mate_lib.errors import AnonyMateError, MissingResourceError, TaskFailedError
from anony_mate_lib.models.blacklist_type import BlacklistType
from anony_mate_lib.models.entity_type import EntityType
from anony_mate_lib.models.redact_models import (
    MarkAnnotationInput,
    MarkBox,
    PdfAnnotationsOutput,
    RedactInput,
    RedactOutput,
    RedactPdfOptions,
)
from anony_mate_lib.models.tasks import TaskAccepted, TaskState
from anony_mate_lib.resources import get_file_resource, get_json_resource
from anony_mate_lib.tasks import poll_status

__all__ = [
    "DEFAULT_TIMEOUT",
    "AnonyMateClient",
    "AnonyMateError",
    "BlacklistType",
    "EntityType",
    "MarkAnnotationInput",
    "MarkBox",
    "MissingResourceError",
    "PdfAnnotationsOutput",
    "RedactInput",
    "RedactOutput",
    "RedactPdfOptions",
    "TaskAccepted",
    "TaskFailedError",
    "TaskState",
    "get_blacklist",
    "get_entity_types",
    "get_file_resource",
    "get_json_resource",
    "poll_status",
]
