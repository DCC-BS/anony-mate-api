"""The wire models of the AnonyMate API: requests, responses and results.

This package is shared verbatim between the server
(``anony_mate_api.models``) and the Python client (``anony_mate_lib.models``,
a symlink to this directory). Imports inside it MUST stay relative so the
same files work under both package names — an absolute
``anony_mate_lib``/``anony_mate_api`` import here breaks the other package.
"""

from .blacklist_type import BlacklistType
from .entity_type import EntityType
from .redact_models import (
    AnyBox,
    DocumentRedactOutput,
    Entity,
    MarkAnnotationInput,
    MarkAnnotationOutput,
    MarkBox,
    PdfAnnotationsOutput,
    RedactBatchInput,
    RedactFileOptions,
    RedactInput,
    RedactionSettings,
    RedactOutput,
    RedactPdfAnnotateInput,
    RedactPdfOptions,
)
from .tasks import TaskAccepted, TaskState

__all__ = [
    "AnyBox",
    "BlacklistType",
    "DocumentRedactOutput",
    "Entity",
    "EntityType",
    "MarkAnnotationInput",
    "MarkAnnotationOutput",
    "MarkBox",
    "PdfAnnotationsOutput",
    "RedactBatchInput",
    "RedactFileOptions",
    "RedactInput",
    "RedactOutput",
    "RedactPdfAnnotateInput",
    "RedactPdfOptions",
    "RedactionSettings",
    "TaskAccepted",
    "TaskState",
]
