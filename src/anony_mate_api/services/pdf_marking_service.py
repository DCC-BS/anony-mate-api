"""Marking a PDF for redaction instead of turning it into text.

The same question as the text path — what in this document is sensitive — is
asked of the same model over the same kind of text, but each word of that text
knows where it stands on the page. So each detection becomes a redaction mark
on the original PDF, for a person to review and apply in an editor such as
Kofax Power PDF.

Two services take part: docling-serve for the layout, the pictures and every
word with its box, and GLiNER through ``RedactService`` for what is
sensitive. This module puts the answers where they belong.
"""

import asyncio
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from dcc_backend_common.fastapi_error_handling import ApiErrorException
from dcc_backend_common.logger import get_logger
from fastapi import status

from anony_mate_api.models.error_codes import UNREADABLE_PDF
from anony_mate_api.models.marked_pdf import MarkedPdf
from anony_mate_api.models.redact_models import RedactFileOptions, RedactInput
from anony_mate_api.services.document_conversion_service import DocumentConversionService
from anony_mate_api.services.pdf_marks.marks import Mark, annotate
from anony_mate_api.services.pdf_marks.pdf import UnreadableDocument, page_count
from anony_mate_api.services.pdf_marks.words import Layout, Picture, TextUnit, read_layout
from anony_mate_api.services.redact_service import RedactService

logger = get_logger("pdf_marking_service")

#: Between two units, so the model never reads the end of one and the start of
#: the next as one name.
UNIT_SEPARATOR = "\n\n"

#: Picture classes marked whole: a signature is no text to detect, so its
#: class is all there is to go on.
PICTURE_CLASSES = frozenset({"signature"})

#: How much of the smaller of two marks the larger has to cover for the two to
#: be one. A name read twice — once in a page's own invisible text layer, once
#: from the pixels — is marked twice, in boxes of different height around the
#: same words. Distinct names stand apart and are never this close.
SAME_MARK = 0.6

#: Lowest classifier score at which a picture counts as one of those classes.
#: Any guess above it counts, not only the first: a signature the classifier
#: half takes for a logo is still a signature.
PICTURE_CONFIDENCE = 0.2


@dataclass(frozen=True)
class Span:
    """Characters of one unit that a detection covers."""

    unit: int
    start: int
    end: int
    label: str
    text: str
    confidence: float


class PdfMarkingService:
    def __init__(
        self,
        document_conversion_service: DocumentConversionService,
        redact_service: RedactService,
        author: str,
    ) -> None:
        self.document_conversion_service = document_conversion_service
        self.redact_service = redact_service
        self.author = author

    async def mark(
        self,
        pdf: bytes,
        filename: str,
        settings: RedactFileOptions,
        on_status: Callable[[str, int | None], None] | None = None,
        on_progress: Callable[[float | None], None] | None = None,
    ) -> MarkedPdf:
        """The original PDF with a redaction mark on everything detected in it.

        Args:
            pdf: The uploaded PDF, as it came.
            filename: Its name, kept for the result.
            settings: Entity types, threshold and blacklist, as for the text path.
            on_status: Docling's status and queue position while it reads the layout.
            on_progress: The detection's progress, once it runs.

        Raises:
            ApiErrorException: The PDF cannot be read completely, or a service failed.
        """
        pages = _pages(pdf)
        read = await self.document_conversion_service.read_layout(pdf, filename, on_status)
        if not read.pages:
            raise _unreadable(
                "docling-serve returned no words: it does not answer include_word_boxes, which marking a PDF needs"
            )
        unread = [number for number in range(1, pages + 1) if number not in read.pages]
        if unread:
            raise _unreadable(f"Docling read no words on pages {unread}")

        layout = read_layout(read.document, read.pages)
        spans = await self._detect(layout.units, settings, on_progress)
        marks = merge_same(_picture_marks(layout.pictures) + _span_marks(layout, spans))
        logger.info("Marked PDF", filename=filename, pages=len(layout.page_sizes), marks=len(marks))

        marked = await asyncio.to_thread(annotate, pdf, marks, self.author)
        return MarkedPdf(filename=_marked_name(filename), content=marked, marks=len(marks))

    async def _detect(
        self,
        units: list[TextUnit],
        settings: RedactFileOptions,
        on_progress: Callable[[float | None], None] | None,
    ) -> list[Span]:
        """What anonymate finds in the units' text, cut back into spans per unit.

        One call over all of it, so a name found on page 3 is carried to its
        repeats on page 30. Nobody reviews the text in between, so every
        detection is carried everywhere and scraps are dropped.
        """
        text, offsets = join_units(units)
        if not text.strip():
            return []
        output = await self.redact_service.redact(
            RedactInput(
                text=text,
                entity_types=settings.entity_types,
                threshold=settings.threshold,
                blacklist=settings.blacklist,
                repeat_everywhere=True,
                drop_scraps=True,
            ),
            on_progress,
        )
        return [
            span
            for label, entities in output.entities.items()
            for entity in entities
            for span in split(entity.start, entity.end, label, entity.confidence, text, offsets, units)
        ]


def join_units(units: list[TextUnit]) -> tuple[str, list[int]]:
    """The text the model reads, and where each unit starts in it."""
    offsets: list[int] = []
    text = ""
    for unit in units:
        if text:
            text += UNIT_SEPARATOR
        offsets.append(len(text))
        text += unit.text
    return text, offsets


def split(
    start: int, end: int, label: str, confidence: float, text: str, offsets: list[int], units: list[TextUnit]
) -> list[Span]:
    """Cut a detection at unit boundaries, one span per unit it touches."""
    spans = []
    index = max(0, bisect_right(offsets, start) - 1)
    while index < len(offsets) and offsets[index] < end:
        offset = offsets[index]
        lo, hi = max(start, offset), min(end, offset + len(units[index].text))
        if lo < hi:
            spans.append(Span(index, lo - offset, hi - offset, label, text[lo:hi], confidence))
        index += 1
    return spans


def _span_marks(layout: Layout, spans: list[Span]) -> list[Mark]:
    return [
        Mark(box, span.label, span.confidence, span.text)
        for span in spans
        for box in layout.boxes(span.unit, span.start, span.end)
    ]


def merge_same(marks: list[Mark]) -> list[Mark]:
    """One mark where two cover the same words.

    A page whose own text layer is invisible — a scan, a publication copy —
    holds every name twice: once as text nobody sees, once in the picture OCR
    read. Both have to go, and one box over both says so once. The box is the
    union, so nothing either of them covered is left out.
    """
    merged: list[Mark] = []
    for mark in marks:
        for index, other in enumerate(merged):
            if other.label != mark.label:
                continue
            if max(mark.box.covered_by(other.box), other.box.covered_by(mark.box)) < SAME_MARK:
                continue
            keep = other if other.confidence >= mark.confidence else mark
            merged[index] = Mark(other.box.union(mark.box), keep.label, keep.confidence, keep.text)
            break
        else:
            merged.append(mark)
    return merged


def _picture_marks(pictures: list[Picture]) -> list[Mark]:
    marks = []
    for picture in pictures:
        found = [(picture.classes.get(name, 0.0), name) for name in PICTURE_CLASSES]
        confidence, name = max(found)
        if confidence >= PICTURE_CONFIDENCE:
            marks.append(Mark(picture.box, name, confidence, text=""))
    return marks


def _pages(pdf: bytes) -> int:
    try:
        return page_count(pdf)
    except UnreadableDocument as error:
        raise _unreadable(str(error)) from error


def _unreadable(message: str) -> ApiErrorException:
    logger.warning("PDF cannot be marked", reason=message)
    return ApiErrorException({
        "errorId": UNREADABLE_PDF,
        "status": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "debugMessage": message,
    })


def _marked_name(filename: str) -> str:
    """``bericht.pdf`` becomes ``bericht.markiert.pdf``."""
    path = Path(filename)
    return f"{path.stem}.markiert{path.suffix or '.pdf'}"
